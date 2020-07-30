import time
import torch
from hparams import load_hparams
from data_ulils import TextMelLoader, TextMelCollate
from torch.utils.data import DataLoader
from model import NeuralConcatenativeSpeechSynthesis
from loss_function import NeuralConcatenativeLoss
import matplotlib.pyplot as plt

def prepare_dataloaders(hparams):
    # Get data, data loaders and collate function ready
    trainset = TextMelLoader(hparams.training_files, hparams)
    valset = TextMelLoader(hparams.validation_files, hparams)
    collate_fn = TextMelCollate()

    train_loader = DataLoader(trainset, num_workers=0, shuffle=True,
                              sampler=None,
                              batch_size=hparams.batch_size, pin_memory=False,
                              drop_last=True, collate_fn=collate_fn)
    return train_loader, valset, collate_fn


def train(hparams):
    torch.manual_seed(hparams.seed)
    torch.cuda.manual_seed(hparams.seed)

    model = NeuralConcatenativeSpeechSynthesis(hparams)
    print("parameter numbers: ", sum(p.numel() for p in model.parameters() if p.requires_grad))
    learning_rate = hparams.learning_rate
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)

    criterion = NeuralConcatenativeLoss()
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    # move model to cuda
    model.to(device)
    train_loader, valset, collate_fn = prepare_dataloaders(hparams)

    iteration = 0
    loss_list = []
    for epoch in range(hparams.epochs):
        print("Epoch: {}".format(epoch))
        running_loss = 0.0
        for i, batch in enumerate(train_loader):
            # model.zero_grad()
            x, y = model.parse_batch(batch)
            y_pred = model(x)
            loss = criterion(y_pred, y)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

            # loss log and visualization
            running_loss += loss.item()
            if i%100 == 0:
                print('[%d, %5d] loss: %.3f' %
                      (epoch + 1, i + 1, running_loss / 2000))
                loss_list.append(running_loss / 2000)
                running_loss = 0.0
                plt.plot(loss_list)

    torch.save(obj=model.state_dict(), f=hparams.model_save_path)

# model.load_state_dict(torch.load(hparams.model_save_path))

if __name__ == "__main__":
    hparams = load_hparams()
    train(hparams)