import torch
from hparams import load_hparams
from data_ulils import TextMelLoader, TextMelCollate
from torch.utils.data import DataLoader
from model import NeuralConcatenativeSpeechSynthesis
from loss_function import NeuralConcatenativeLoss
import matplotlib.pyplot as plt
import time
import math
from tensorboardX import SummaryWriter
from prefetch_generator import BackgroundGenerator
from data_ulils import get_mel_text_pair_inference
import librosa
import librosa.display
import numpy as np
from utils import dynamic_range_compression, dynamic_range_decompression
import io
import PIL.Image
from torchvision.transforms import ToTensor
from scipy.io.wavfile import read


def time_since(since):
    s = time.time()-since
    m = math.floor(s / 60)
    s -= m*60
    return m, s


def gen_plot(melspectrogram, mel_outputs, hparams):
    """Create a pyplot plot and save to buffer."""
    if mel_outputs.shape[1] == 1000:
        mel_outputs = mel_outputs[:, :melspectrogram.shape[1]]
    buf = io.BytesIO()
    plt.figure(figsize=(10, 4))
    plt.subplot(2, 1, 1)
    librosa.display.specshow(melspectrogram, y_axis='mel', x_axis='time',
                             hop_length=hparams.hop_length, fmin=hparams.mel_fmin, fmax=hparams.mel_fmax)
    plt.title('Original Mel spectrogram')
    plt.subplot(2, 1, 2)
    try:
        librosa.display.specshow(mel_outputs, y_axis='mel', x_axis='time',
                                 hop_length=hparams.hop_length, fmin=hparams.mel_fmin, fmax=hparams.mel_fmax)
    except IndexError as e:
        print("IndexError", e)
    plt.title('Generated Mel spectrogram')
    plt.savefig(buf, format='jpeg')
    buf.seek(0)
    plt.close()
    return buf


def atten_matrix_plot(writer, step, train_matrix, infer_matrix, title):
    buf = io.BytesIO()
    fig = plt.figure()
    ax = fig.add_subplot(2, 1, 1)
    cax = ax.matshow(train_matrix.data.numpy())
    fig.colorbar(cax)
    ax.grid(False)
    plt.tight_layout()
    plt.title("train matrix")

    ax = fig.add_subplot(2, 1, 2)
    cax = ax.matshow(infer_matrix.data.numpy())
    fig.colorbar(cax)
    ax.grid(False)
    plt.tight_layout()
    plt.title("infer matrix")

    plt.savefig(buf, format='jpeg')
    buf.seek(0)
    plt.close()
    image = PIL.Image.open(buf)
    image = ToTensor()(image)
    writer.add_image(title, image, step)


def gen_audio(melspectrogram, mel_outputs, hparams):
    melspectrogram = dynamic_range_decompression(melspectrogram)
    mel_outputs = dynamic_range_decompression(mel_outputs)
    if mel_outputs.shape[1] == 1000:
        mel_outputs = mel_outputs[:, :melspectrogram.shape(1)]
    original_audio_signal = librosa.feature.inverse.mel_to_audio(melspectrogram.data.numpy(), sr=hparams.sampling_rate,
                                                                 n_fft=hparams.filter_length,
                                                                 hop_length=hparams.filter_length, power=1)
    gen_audio_signal = librosa.feature.inverse.mel_to_audio(mel_outputs.data.numpy(), sr=hparams.sampling_rate,
                                                            n_fft=hparams.filter_length,
                                                            hop_length=hparams.filter_length, power=1)
    return original_audio_signal, gen_audio_signal


def prepare_dataloaders(hparams):
    # Get data, data loaders and collate function ready
    trainset = TextMelLoader(hparams.training_files, hparams)
    valset = TextMelLoader(hparams.validation_files, hparams)
    collate_fn = TextMelCollate()

    train_loader = DataLoader(trainset, num_workers=2, shuffle=False,
                              sampler=None,
                              batch_size=hparams.batch_size, pin_memory=False,
                              drop_last=True, collate_fn=collate_fn)
    return train_loader, valset, collate_fn


def validate(model, criterion, valset, batch_size, collate_fn):
    """Handles all the validation scoring and printing"""
    model.eval()
    with torch.no_grad():
        val_loader = DataLoader(valset, sampler=None, num_workers=2,
                                shuffle=False, batch_size=batch_size,
                                pin_memory=False, collate_fn=collate_fn)
        val_loss = 0.0
        total_mel_loss = 0.0
        total_gate_loss = 0.0
        for i, batch in enumerate(BackgroundGenerator(val_loader)):
            x, y = model.parse_batch(batch)
            y_pred = model(x)
            loss, mel_loss, gate_loss = criterion(y_pred, y)
            val_loss += loss.item()
            total_mel_loss += mel_loss
            total_gate_loss += gate_loss
        val_loss = val_loss / (i + 1)
        total_mel_loss = total_mel_loss / (i + 1)
        total_gate_loss = total_gate_loss / (i + 1)
    model.train()
    return val_loss, total_mel_loss, total_gate_loss


def train(hparams):
    torch.cuda.manual_seed(hparams.seed)
    model = NeuralConcatenativeSpeechSynthesis(hparams)
    model.train()
    print(model)
    print("parameter numbers: ", sum(p.numel() for p in model.parameters() if p.requires_grad))
    learning_rate = hparams.learning_rate
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)

    criterion = NeuralConcatenativeLoss()
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    # move model to cuda
    model.to(device)
    train_loader, valset, collate_fn = prepare_dataloaders(hparams)

    running_loss = 0.0
    writer = SummaryWriter(hparams.exp_path)
    iter_num = len(train_loader)
    # text = "Their original capital had been a few shillings, and for this they purchased the right to tax their fellows to the extent of pounds per week."
    inputs = get_mel_text_pair_inference(hparams)
    for epoch in range(hparams.epochs):
        print("Epoch: {}".format(epoch))
        for i, batch in enumerate(BackgroundGenerator(train_loader)):
            # print(len(train_loader))
            x, y = model.parse_batch(batch)
            y_pred, align1_attention_weights, align2_attention_weights, attention_weights = model(x)
            loss, mel_loss, gate_loss = criterion(y_pred, y)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            if i%10 == 0:
                step = epoch * iter_num + i
                print('[%d, %d] loss: %.3f' % (epoch, step, running_loss / 10))
                running_loss = 0.0
                # val_loss, total_mel_loss, total_gate_loss = validate(model, criterion, valset, hparams.batch_size, collate_fn)
                # writer.add_scalar("val_loss", val_loss, epoch*iter_num+i)
                # writer.add_scalar("val_mel_loss", total_mel_loss, epoch*iter_num+i)
                # writer.add_scalar("val_gate_loss", total_gate_loss, epoch*iter_num+i)

                # training mel spectrogram
                plot_buf = gen_plot(x[2].cpu().data.numpy()[0], y_pred[0].cpu().data.numpy()[0], hparams)
                image = PIL.Image.open(plot_buf)
                image = ToTensor()(image)
                writer.add_image('training mel spectrogram', image, step)

                # inference mel spectrogram
                # sample_rate, audio = read("/home/swl/LJSpeech-1.1/wavs/LJ006-0115.wav")
                sample_rate, audio = read("/Users/swl/Dissertation/LJSpeech-1.1/wavs/LJ006-0115.wav")
                # inference_model = NeuralConcatenativeSpeechSynthesis(hparams)
                # if torch.cuda.is_available():
                #     inference_model.load_state_dict(torch.load(hparams.model_save_path))
                # else:
                #     inference_model.load_state_dict(torch.load(hparams.model_save_path, map_location=torch.device('cpu')))
                # inference_model.to(device)
                # original_mel, mel_predicted = inference(inference_model, inputs, audio, hparams)
                original_mel, mel_predicted, infer_align1_attention_weights, infer_align2_attention_weights, infer_attention_weights = \
                    inference(model, inputs, audio, hparams)
                atten_matrix_plot(writer, step, align1_attention_weights[0].cpu(), infer_align1_attention_weights[0].cpu(), "alignment1")
                atten_matrix_plot(writer, step, align2_attention_weights[0].cpu(), infer_align2_attention_weights[0].cpu(), "alignment2")
                atten_matrix_plot(writer, step, torch.t(attention_weights[0].cpu()), torch.t(infer_attention_weights[0].cpu()), "decoder attention")
                plot_buf = gen_plot(original_mel, mel_predicted, hparams)
                image = PIL.Image.open(plot_buf)
                image = ToTensor()(image)
                writer.add_image('inference mel spectrogram', image, step)

                # audio during training
                #orig_audio, gener_audio = gen_audio(batch[2].cpu().data[0], y_pred[0].cpu().data[0], hparams)
                #writer.add_audio("original_audio", orig_audio, sample_rate=hparams.sampling_rate)
                #writer.add_audio("generated_audio", gener_audio, sample_rate=hparams.sampling_rate)

            # loss log and visualization
            running_loss += loss.item()
            writer.add_scalar("training_loss", loss.item(), epoch*iter_num+i)
            writer.add_scalar("mel_loss", mel_loss, epoch*iter_num+i)
            writer.add_scalar("gate_loss", gate_loss, epoch*iter_num+i)
            del loss
            del y_pred
        torch.save(obj=model.state_dict(), f=hparams.model_save_path)
        print("Epoch: %d; Save model!" % (epoch))


def inference(model, inputs, original_audio, hparams):
    model.eval()
    with torch.no_grad():
        mel_outputs, gate_outputs, align1_attention_weights, align2_attention_weights, attention_weights = model.inference(inputs)
    audio_norm = original_audio / hparams.max_wav_value
    melspectrogram = librosa.feature.melspectrogram(y=audio_norm, sr=22050, n_fft=1024, hop_length=256, power=1,
                                                    n_mels=hparams.n_mel_channels, fmin=hparams.mel_fmin,
                                                    fmax=hparams.mel_fmax)
    frame_num = melspectrogram.shape[1]
    print("Frame number of ground truth: ", frame_num)
    mel_outputs = mel_outputs.cpu().data.numpy().T
    model.train()
    return np.log(melspectrogram), mel_outputs, align1_attention_weights, align2_attention_weights, attention_weights


def inference_local(model, inputs, original_audio, hparams):
    model.eval()
    with torch.no_grad():
        mel_outputs, gate_outputs = model.inference(inputs)
    melspectrogram = librosa.feature.melspectrogram(y=original_audio, sr=22050, n_fft=1024, hop_length=256, power=1,
                                                    n_mels=hparams.n_mel_channels, fmin=hparams.mel_fmin,
                                                    fmax=hparams.mel_fmax)
    frame_num = melspectrogram.shape[1]
    # mel_outputs = mel_outputs.data.numpy()[:frame_num, :].T
    mel_outputs = mel_outputs.data.numpy().T

    plt.figure(figsize=(10, 4))
    plt.subplot(2, 1, 1)
    librosa.display.specshow(np.log(melspectrogram), y_axis='mel', x_axis='time',
                             hop_length=hparams.hop_length, fmin=hparams.mel_fmin, fmax=hparams.mel_fmax)
    plt.title('Original Mel spectrogram')
    plt.subplot(2, 1, 2)
    librosa.display.specshow(mel_outputs, y_axis='mel', x_axis='time',
                             hop_length=hparams.hop_length, fmin=hparams.mel_fmin, fmax=hparams.mel_fmax)
    plt.title('Generated Mel spectrogram')
    plt.tight_layout()
    plt.show()


if __name__ == "__main__":
    hparams = load_hparams()
    torch.manual_seed(hparams.seed)
    train(hparams)
    # batch = train(hparams)
    # plt.figure(figsize=(10, 4))
    # plt.subplot(1, 1, 1)
    # melspectrogram = batch[2].data.numpy()[2]
    # librosa.display.specshow(melspectrogram, y_axis='mel', x_axis='time',
    #                          hop_length=hparams.hop_length, fmin=hparams.mel_fmin, fmax=hparams.mel_fmax)
    # plt.show()

    # text = "Only proteid foods form new protoplasm"
    # inputs = get_mel_text_pair_inference(text, hparams)
    # model = NeuralConcatenativeSpeechSynthesis(hparams)
    # model.eval()
    # if torch.cuda.is_available():
    #     model.load_state_dict(torch.load('NeuralConcate_exp_3.pth'))
    # else:
    #     model.load_state_dict(torch.load('NeuralConcate_exp_3.pth', map_location=torch.device('cpu')))
    # y, sample_rate = librosa.core.load("/Users/swl/Dissertation/LJSpeech-1.1/wavs/LJ026-0113.wav", sr=22050)
    # inference_local(model, inputs, y, hparams)
