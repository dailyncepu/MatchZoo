"""Transformer layer referenced from Bert."""

import math
import typing

import keras
from keras.engine import Layer
from keras import backend as K

import matchzoo


class Transformer(Layer):
    """Bert version transformer layer.

    See: https://github.com/google-research/bert
    """

    def __init__(self,
                 hidden_size: int,
                 num_heads: int,
                 ffn_size: int,
                 attention_probs_dropout_prob: float,
                 hidden_dropout_prob: float,
                 initializer_stddev: float = 0.02,
                 query_act: typing.Union[typing.Callable, str, None] = None,
                 key_act: typing.Union[typing.Callable, str, None] = None,
                 value_act: typing.Union[typing.Callable, str, None] = None,
                 ffn_act: typing.Union[typing.Callable, str, None] = None,
                 **kwargs):
        """Layer parameters initialization."""
        super().__init__(**kwargs)
        if hidden_size % num_heads != 0:
            raise ValueError(f"The hidden size {hidden_size} is not a"
                             f" multiple of the number of attention heads"
                             f" {num_heads}")
        self._hidden_size = hidden_size
        self._num_heads = num_heads
        self._size_per_head = int(hidden_size / num_heads)
        self._ffn_size = ffn_size
        self._attention_probs_dropout_prob = attention_probs_dropout_prob
        self._hidden_dropout_prob = hidden_dropout_prob
        self._initializer_stddev = initializer_stddev
        self._query_act = query_act
        self._key_act = key_act
        self._value_act = value_act
        self._ffn_act = ffn_act

    def build(self, input_shape: list):
        """Build layer."""
        if len(input_shape) < 3:
            raise ValueError("Input 3 tensor at least as "
                             "`query`, `key` and `value`.")

        self._from_len = input_shape[0][1]
        self._to_len = input_shape[1][1]
        if input_shape[2][1] != self._to_len:
            raise ValueError(f"Value tensor have length {input_shape[2][1]} "
                             f"!= key length {self._to_len}")

        # attention layers
        self._query_proj = keras.layers.Dense(
            self._hidden_size,
            activation=self._query_act,
            kernel_initializer=keras.initializers.TruncatedNormal(
                stddev=self._initializer_stddev)
        )
        self._key_proj = keras.layers.Dense(
            self._hidden_size,
            activation=self._key_act,
            kernel_initializer=keras.initializers.TruncatedNormal(
                stddev=self._initializer_stddev)
        )
        self._value_proj = keras.layers.Dense(
            self._hidden_size,
            activation=self._value_act,
            kernel_initializer=keras.initializers.TruncatedNormal(
                stddev=self._initializer_stddev)
        )
        self._attn_proj = keras.layers.Dense(
            self._hidden_size,
            kernel_initializer=keras.initializers.TruncatedNormal(
                stddev=self._initializer_stddev)
        )
        self._attn_layer_norm = matchzoo.layers.LayerNormalization()

        # ffn layers
        self._intermediate_layer = keras.layers.Dense(
            self._ffn_size,
            activation=self._ffn_act,
            kernel_initializer=keras.initializers.TruncatedNormal(
                stddev=self._initializer_stddev)
        )
        self._output_layer = keras.layers.Dense(
            self._hidden_size,
            kernel_initializer=keras.initializers.TruncatedNormal(
                stddev=self._initializer_stddev)
        )
        self._output_layer_norm = matchzoo.layers.LayerNormalization()

        super().build(input_shape)

    def call(self, x: list):
        """Layer call."""
        if len(x) == 3:
            x.append(None)
        query, key, value, attention_mask = x

        # projection: B x L x H -> B x L x H
        query_layer = self._query_proj(query)
        key_layer = self._key_proj(key)
        value_layer = self._value_proj(value)

        # split heads: -> B x N x L x D
        query_layer = K.reshape(
            query_layer,
            [-1, self._from_len, self._num_heads, self._size_per_head])
        query_layer = K.permute_dimensions(query_layer, [0, 2, 1, 3])
        key_layer = K.reshape(
            key_layer,
            [-1, self._to_len, self._num_heads, self._size_per_head])
        key_layer = K.permute_dimensions(key_layer, [0, 2, 1, 3])
        value_layer = K.reshape(
            value_layer,
            [-1, self._to_len, self._num_heads, self._size_per_head])
        value_layer = K.permute_dimensions(value_layer, [0, 2, 1, 3])

        # attention -> B x N x L1 x L2
        attn_score = K.batch_dot(query_layer, key_layer, axes=(3, 3))
        attn_score = 1.0 / math.sqrt(float(self._size_per_head)) * attn_score

        if attention_mask is not None:
            attention_mask = K.expand_dims(attention_mask, 1)
            bias = (1.0 - K.cast(attention_mask, 'float32')) * -10000.0
            attn_score += bias

        attn_probs = K.softmax(attn_score)
        attn_probs = K.dropout(attn_probs, self._attention_probs_dropout_prob)

        # matmul: -> B x N x L x D -> B x L x H
        context_layer = K.batch_dot(value_layer, attn_probs, axes=(2, 3))
        context_layer = K.permute_dimensions(context_layer, [0, 2, 1, 3])
        context_layer = K.reshape(context_layer,
                                  [-1, self._from_len, self._hidden_size])

        # output projection: -> B x L x H
        attn_output = self._attn_proj(context_layer)
        attn_output = K.dropout(attn_output, self._hidden_dropout_prob)
        attn_output = self._attn_layer_norm(attn_output + query)

        # ffn
        intermediate = self._intermediate_layer(attn_output)
        output = self._output_layer(intermediate)
        output = K.dropout(output, self._hidden_dropout_prob)
        output = self._output_layer_norm(output + attn_output)

        return output

    def compute_output_shape(self, input_shape: list) -> list:
        """Get output tensor shape."""
        return input_shape[0]

    def get_config(self) -> dict:
        """Layer configuration."""
        config = {
            'hidden_size': self._hidden_size,
            'num_heads': self._num_heads,
            'ffn_size': self._ffn_size,
            'attention_probs_dropout_prob': self._attention_probs_dropout_prob,
            'hidden_dropout_prob': self._hidden_dropout_prob,
            'initializer_stddev': self._initializer_stddev,
            'query_act': keras.activation.serialize(self._query_act),
            'key_act': keras.activation.serialize(self._key_act),
            'value_act': keras.activation.serialize(self._value_act),
            'ffn_act': keras.activation.serialize(self._ffn_act)
        }
        base_config = super().get_config()
        return {**base_config, **config}
