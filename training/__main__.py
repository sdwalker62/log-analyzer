from Transformer import Transformer
from MultiHeadAttention import MultiHeadAttention
from tqdm import tqdm
from Metrics import loss_function
from Metrics import accuracy_function
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import LabelEncoder
from sklearn.metrics import classification_report
from sklearn.metrics import accuracy_score

from einops import rearrange

import tensorflow as tf
import os
import numpy as np
import joblib
import pandas as pd
import logging
import sqlite3 as sql
import time

num_layers = int(os.environ["TRANSFORMER_LAYERS"])
d_model = int(os.environ["W2V_EMBED_SIZE"])
dff = int(os.environ["TRANSFORMER_DFF"])
num_heads = int(os.environ["TRANSFORMER_HEADS"])
batch_size = int(os.environ["BATCH_SIZE"])
training = bool(int(os.environ["TRAINING"]))
epochs = int(os.environ["EPOCHS"])
max_seq_len = 200


class CustomSchedule(tf.keras.optimizers.schedules.LearningRateSchedule):

    def __init__(self, d_model: int, warmup_steps=4000):
        super(CustomSchedule, self).__init__()

        self.d_model = d_model
        self.d_model = tf.cast(self.d_model, tf.float32)
        self.warmup_steps = warmup_steps

    def __call__(self, step):
        arg1 = tf.math.rsqrt(step)
        arg2 = step * (self.warmup_steps ** -1.5)

        return tf.math.rsqrt(self.d_model) * tf.math.minimum(arg1, arg2)


learning_rate = CustomSchedule(d_model)

optimus_prime = None
adm_optimizer = tf.keras.optimizers.Adam(learning_rate, beta_1=0.9, beta_2=0.98, epsilon=1e-9)

epoch_loss = tf.keras.metrics.Mean(name='train_loss')
epoch_accuracy = tf.keras.metrics.Mean(name='train_accuracy')

train_step_signature = [
    tf.TensorSpec(shape=([batch_size, None]), dtype=tf.float32),
    tf.TensorSpec(shape=([batch_size]), dtype=tf.float32)
]

add_att_layer = tf.keras.layers.AdditiveAttention()
softmax = tf.keras.layers.Softmax()
lr = LogisticRegression()

s1 = tf.keras.Sequential([
    tf.keras.layers.Dense(512),
    tf.keras.layers.Dense(4),
    tf.keras.layers.Softmax()
])

@tf.function(input_signature=train_step_signature)
def train_step(log_batch: tf.Tensor, labels: tf.Tensor):

    transformer_input = tf.tuple([
        log_batch,  # <tf.Tensor: shape=(batch_size, max_seq_len), dtype=float32>
        labels  # <tf.Tensor: shape=(batch_size, num_classes), dtype=float32>
    ])
    
    with tf.GradientTape() as tape:
        Rs, _ = optimus_prime.call(transformer_input)

        # a_s = add_att_layer([Rs, Rs])
        # y = softmax(a_s * Rs)

        y = Rs
        
        loss = tf.py_function(loss_function, [labels, y], tf.float32)

        pred = s1(y)
        labels = tf.cast(labels, tf.int64)

    # Optimize the model
    grads = tape.gradient(loss, optimus_prime.trainable_variables)
    adm_optimizer.apply_gradients(zip(grads, optimus_prime.trainable_variables))

    # Tracking Progress
    epoch_loss.update_state(loss)  # Adding Batch Loss
    epoch_accuracy.update_state(accuracy_function(labels, pred))


logging.basicConfig(format='%(asctime)s %(levelname)s | %(message)s',
                    level=logging.INFO)
logger = logging.getLogger(__name__)


def database_builder(path: str) -> pd.DataFrame():
    logger.info('Building DataFrame ...')
    (_, _, files) = next(os.walk(path))
    sql_query = 'SELECT * FROM logs'
    data = []
    for f in files:
        if '.db' in f:
            conn = create_connection(path + f)
            d = pd.read_sql_query(sql_query, conn)
            data.append(d)
    logger.info('...complete!')
    return pd.concat(data)


def create_connection(path: str) -> sql.Connection:
    """
    Creates a database connection
    :param path: str
        path to database object
    :return sql.Connection
        a connection to the database
    """
    try:
        conn = sql.connect(path)
        logger.info('Connected to database ' + path)
        return conn
    except sql.Error as e:
        logger.warning(e)


def get_max_length_(dataset: pd.DataFrame, buffer_size: float) -> int:
    return int((1 + buffer_size) * dataset['log'].str.len().max())


def process_batch(dataset: pd.DataFrame,
                  vocabulary: dict,
                  max_seq_len: int,
                  idx: int,
                  labels: dict) -> tuple:
    logs = np.zeros((batch_size, max_seq_len))
    y_true = np.empty((batch_size,))

    start_window = idx * batch_size
    end_window = (idx + 1) * batch_size
    for log_idx, log in enumerate(dataset['log'][start_window:end_window]):
        for seq_idx, word in enumerate(log.split()):
            if seq_idx >= max_seq_len:
                break
            logs[log_idx, seq_idx] = vocabulary[word] if word in vocabulary.keys() else 0
        y_true[log_idx] = labels[dataset['label'][log_idx]]

    return tf.convert_to_tensor(logs, dtype=tf.float32), tf.convert_to_tensor(y_true, dtype=tf.float32)


if __name__ == '__main__':

    logging.info('Loading assets')
    word_embedding_matrix = joblib.load("/results/w2v_weights.joblib")
    vocabulary = joblib.load("/results/vocab_dict.joblib")
    dataset = database_builder('/database/')
    dataset = dataset.sample(frac=1).reset_index(drop=True)
    max_seq_len = 200  # get_max_length_(dataset, 0.0)
    vocab_size = len(vocabulary)

    logging.info('Processing logs for training')
    label_unique = dataset['label'].unique()
    lbp = LabelEncoder().fit(label_unique)
    binary_labels = lbp.transform(label_unique)

    log_labels = {}
    for idx, label in enumerate(label_unique):
        log_labels.update({
            label: binary_labels[idx]
        })

    n_logs = len(dataset.index)
    n_iter = n_logs // batch_size
    remainder = n_logs % batch_size

    optimus_prime = Transformer(
        num_layers,
        d_model,
        num_heads,
        dff,
        vocab_size,
        word_embedding_matrix,
        max_seq_len,
        rate=0.1)

    optimizer = tf.keras.optimizers.Adam(learning_rate, beta_1=0.9, beta_2=0.98, epsilon=1e-9)

    checkpoint_path = "./checkpoints/train"
    checkpoint = tf.train.Checkpoint(step=tf.Variable(1), transformer=optimus_prime, optimizer=optimizer)
    checkpoint_manager = tf.train.CheckpointManager(checkpoint, checkpoint_path, max_to_keep=5)

    # if a checkpoint exists, restore the latest checkpoint.
    if checkpoint_manager.latest_checkpoint:
        checkpoint.restore(checkpoint_manager.latest_checkpoint)
        print('Latest checkpoint restored!!')

    for epoch in tqdm(range(epochs)):

        start = time.time()

        epoch_loss.reset_states()
        epoch_accuracy.reset_states()

        for idx in range(n_iter):
            log_batch, labels = process_batch(dataset, vocabulary, max_seq_len, idx, log_labels)
                
            # Returns Eager Tensor for Predictions
            train_step(log_batch, labels)
            checkpoint.step.assign_add(1)

            if int(checkpoint.step) % 10 == 0:
                save_path = checkpoint_manager.save()
                print(f'Saved checkpoint for step {int(checkpoint.step)}: {save_path}')
                print(f'Loss {epoch_loss.result():.3f}, Accuracy: {epoch_accuracy.result():.3%}')

        print("Epoch {:03d}: Loss: {:.3f}, Accuracy: {:.3%}".format(epoch,
                                                                    epoch_loss.result(),
                                                                    epoch_accuracy.result()))

        print(f'Time taken for 1 epoch: {time.time() - start:.2f} secs\n')
