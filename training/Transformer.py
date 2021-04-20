import tensorflow as tf
from TransformerBlock import TransformerBlock
from PositionalEncoder import PositionalEncoding
import os
import time

training = bool(os.environ["TRAINING"])

class Transformer(tf.keras.Model):

    def __init__(self,
                 num_layers,
                 d_model,
                 num_heads,
                 dff,
                 input_vocab_size,
                 embedding_matrix,
                 max_seq_len,
                 rate=0.1):
        super(Transformer, self).__init__()

        self.d_model = d_model

        self.embedding = tf.keras.layers.Embedding(
            input_vocab_size,
            d_model,
            weights=[embedding_matrix],
            input_length=max_seq_len)

        self.pos_encoding = PositionalEncoding(max_seq_len, d_model)

        self.transformer_blocks = [TransformerBlock(
                        num_layers,
                        d_model,
                        embedding_matrix,
                        num_heads,
                        dff,
                        input_vocab_size,
                        max_seq_len,
                        rate) for _ in range(3)]

        self.dropout = tf.keras.layers.Dropout(rate)

    # def call(self, inp, tar, enc_padding_mask,
    #         look_ahead_mask, dec_padding_mask):
    def call(self, input_tuple: tf.tuple, **kwargs):
        log_batch = input_tuple[0]
        encoding_padding_mask = None # input_tuple[1]

        # adding embedding and position encoding.
        embedding_tensor = self.embedding(log_batch, training=training)  # (batch_size, input_seq_len, d_model)
        embedding_tensor *= tf.math.sqrt(tf.cast(self.d_model, tf.float32))  # (batch_size, input_seq_len, d_model)
        embedding_tensor = self.pos_encoding(embedding_tensor)
        embedding_tensor = self.dropout(embedding_tensor, training=training)

        # Transformer Block #1
        # (batch_size, inp_seq_len, d_model), (batch_size, class, inp_seq_len, inp_seq_len)
        enc_output, att = self.transformer_blocks[0](embedding_tensor, encoding_padding_mask)

        # Transformer Block #2 vv (takes the place of the Decoder)
        fin_output, att = self.transformer_blocks[1](enc_output, encoding_padding_mask)

        final_output = tf.reduce_mean(fin_output, axis=1)

        out, att = self.transformer_blocks[2](final_output, encoding_padding_mask)

        seq_representation = tf.reduce_mean(out, axis=1)

        return seq_representation, att