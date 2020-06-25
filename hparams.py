import tensorflow as tf


_pad = '_'
_punctuation = '!\'(),.:;? '
_special = '-'
_letters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'

# Export all symbols:
symbols = [_pad] + list(_special) + list(_punctuation) + list(_letters)

def load_hparams():
    hparams = tf.contrib.training.HParams(
        ################################
        # Experiment Parameters        #
        ################################
        epochs=500,
        seed=1234,


        ################################
        # Data Parameters             #
        ################################
        training_files='filelists/ljs_audio_text_train_filelist.txt',
        validation_files='filelists/ljs_audio_text_val_filelist.txt',

        ################################
        # Audio Parameters             #
        ################################
        max_wav_value=32768.0,
        sampling_rate=22050,
        filter_length=1024,
        hop_length=256,
        win_length=1024,
        n_mel_channels=80,
        mel_fmin=0.0,
        mel_fmax=8000.0,

        ################################
        # Model Parameters             #
        ################################
        n_symbols=len(symbols),
        symbols_embedding_dim=512,

        # Decoder parameters
        n_frames_per_step=1,  # currently only 1 is supported

        ################################
        # Optimization Hyperparameters #
        ################################
        batch_size=4
    )

    return hparams