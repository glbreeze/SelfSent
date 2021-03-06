import tensorflow as tf
import time
import utils_nlp
import re


class SelfSent(object):

    def __init__(self, dataset, parameters):
        self.verbose = parameters['verbose']
        self.dataset = dataset

        # Placeholders for input, output and dropout
        self.input_token_indices = tf.placeholder(tf.int32, [parameters['batch_size'], self.dataset.max_tokens], name="input_token_indices")
        self.input_token_lengths = tf.placeholder(tf.int32, [parameters['batch_size']], name="input_token_lengths")
        self.input_label_vector_indices = tf.placeholder(tf.float32, [parameters['batch_size'], dataset.number_of_classes], name="input_label_indices")
        self.dropout_keep_prob = tf.placeholder(tf.float32, name="dropout_keep_prob")

        # Internal parameters
        initializer = tf.contrib.layers.xavier_initializer()

        # Token embedding layer
        with tf.variable_scope("token_embedding"):
            self.token_embedding_weights = tf.get_variable("token_embedding_weights", shape=[dataset.vocabulary_size, parameters['token_embedding_dimension']], initializer=initializer, trainable=not parameters['freeze_token_embeddings'])
            token_lstm_input = tf.nn.embedding_lookup(self.token_embedding_weights, self.input_token_indices)
            if self.verbose: print("token_lstm_input: {0}".format(token_lstm_input))

        # Add dropout
        with tf.variable_scope("dropout"):
            token_lstm_input_drop = tf.nn.dropout(token_lstm_input, self.dropout_keep_prob, name='token_lstm_input_drop')
            if self.verbose: print("token_lstm_input_drop: {0}".format(token_lstm_input_drop))

        # BiLSTM
        with tf.variable_scope("token_lstm"):
            H = bidirectional_LSTM(token_lstm_input_drop, parameters['lstm_hidden_state_dimension'], initializer, parameters['batch_size'], self.input_token_lengths)
            if self.verbose: print("H: {0}".format(H))

        # Because we use batch, H is a 3D matrix while Ws1 and Ws2 are 2D matrix.
        # To simplify the computation:
        # M = A * H = softmax(Ws2 * tanh(Ws1 * H^T)) * H
        # M^T = H^T * A^T = H^T * softmax(tanh(H * Ws1^T) * Ws2^T)

        # Attention
        with tf.variable_scope("attention"):
            Ws1 = tf.get_variable("Ws1", shape=[2 * parameters['lstm_hidden_state_dimension'], parameters['da']], initializer=initializer)
            if self.verbose: print("Ws1: {0}".format(Ws1))

            H_reshaped = tf.reshape(H, [-1, 2 * parameters['lstm_hidden_state_dimension']], name='H_reshaped')
            tanh_Ws1_time_H = tf.nn.tanh(tf.matmul(H_reshaped, Ws1), name="tanh_Ws1_time_H")
            if self.verbose: print("tanh_Ws1_time_H: {0}".format(tanh_Ws1_time_H))

            Ws2 = tf.get_variable("Ws2", shape=[parameters['da'], parameters['r']], initializer=initializer)
            if self.verbose: print("Ws2: {0}".format(Ws2))

            tanh_Ws1_time_H_and_time_Ws2 = tf.matmul(tanh_Ws1_time_H, Ws2, name="tanh_ws1_time_H_and_time_Ws2")
            if self.verbose: print("tanh_Ws1_time_H_and_time_Ws2: {0}".format(tanh_Ws1_time_H_and_time_Ws2))

            # The final softmax should be applied for the dimension corresponding to the tokens
            self.A_T = tf.nn.softmax(tf.reshape(tanh_Ws1_time_H_and_time_Ws2, shape=[parameters['batch_size'], self.dataset.max_tokens, parameters['r']], name="A_T_no_softmax"), dim=1, name="A_T")
            if self.verbose: print("A_T: {0}".format(self.A_T))

        # Apply Attention
        with tf.variable_scope("apply_attention"):
            H_T = tf.transpose(H, perm=[0, 2, 1], name="H_T")
            M_T = tf.matmul(H_T, self.A_T, name="M_T_no_transposed")
            if self.verbose: print("M_T: {0}".format(M_T))

        # Compute penalization term
        with tf.variable_scope("penalization_term"):
            A = tf.transpose(self.A_T, perm=[0, 2, 1], name="A")
            AA_T = tf.matmul(A, self.A_T, name="AA_T")
            identity = tf.reshape(tf.tile(tf.diag(tf.ones([parameters['r']]), name="diag_identity"), [parameters['batch_size'], 1], name="tile_identity"), [parameters['batch_size'], parameters['r'], parameters['r']], name="identity")
            self.penalized_term = tf.square(tf.norm(AA_T - identity, ord='euclidean', axis=[1, 2], name="frobenius_norm"), name="penalized_term")
            if self.verbose: print("penalized_term: {0}".format(self.penalized_term))

        # Layer ReLU 1
        with tf.variable_scope("layer_ReLU_1"):
            flatten_M_T = tf.reshape(M_T, shape=[parameters['batch_size'], parameters['r'] * 2 * parameters['lstm_hidden_state_dimension']], name="flatten_M_T")

            W_ReLU_1 = tf.get_variable("W_ReLU_1", shape=[parameters['r'] * 2 * parameters['lstm_hidden_state_dimension'], parameters['mlp_hidden_layer_1_units']], initializer=initializer)
            if self.verbose: print("W_ReLU_1: {0}".format(W_ReLU_1))
            b_ReLU_1 = tf.Variable(tf.constant(0.0, shape=[parameters['mlp_hidden_layer_1_units']]), name="bias_ReLU_1")
            if self.verbose: print("b_ReLU_1: {0}".format(b_ReLU_1))

            output_relu_1 = tf.nn.relu(tf.nn.xw_plus_b(flatten_M_T, W_ReLU_1, b_ReLU_1, name="output_layer_1"), name="output_ReLU_1")
            if self.verbose: print("output_relu_1: {0}".format(output_relu_1))

        '''
        Not Sure if in the paper there are 2 hidden layers or only one
        Don't forget to change shape of W_output
        # Layer ReLU 2
        with tf.variable_scope("layer_ReLU_2"):
            W_ReLU_2 = tf.get_variable("W_ReLU_2", shape=[parameters['mlp_hidden_layer_1_units'], parameters['mlp_hidden_layer_2_units']], initializer=initializer)
            if self.verbose: print("W_ReLU_2: {0}".format(W_ReLU_2))
            b_ReLU_2 = tf.Variable(tf.constant(0.0, shape=[parameters['mlp_hidden_layer_2_units']]), name="bias_ReLU_2")
            if self.verbose: print("b_ReLU_2: {0}".format(b_ReLU_2))
            
            output_relu_2 = tf.nn.relu(tf.nn.xw_plus_b(output_relu_1, W_ReLU_2, b_ReLU_2, name="output_layer_2"), name="output_ReLU_2")
        '''

        # Output layer
        with tf.variable_scope("layer_output") :
            W_output = tf.get_variable("W_output", shape=[parameters['mlp_hidden_layer_1_units'], self.dataset.number_of_classes], initializer=initializer)
            if self.verbose: print("W_output: {0}".format(W_output))
            b_output = tf.Variable(tf.constant(0.0, shape=[self.dataset.number_of_classes]), name="bias_output")
            if self.verbose: print("b_output: {0}".format(b_output))

            final_output = tf.nn.xw_plus_b(output_relu_1, W_output, b_output, name="y_hat")
            self.yhat = tf.argmax(final_output, dimension=1, name="predictions")
            self.confidence = tf.reduce_max(tf.nn.softmax(final_output), axis=1, name="confidence")
            if self.verbose: print("final_output: {0}".format(final_output))
            if self.verbose: print("yhat: {0}".format(self.yhat))

        # Loss
        with tf.variable_scope("loss"):
            losses = tf.nn.softmax_cross_entropy_with_logits(logits=final_output, labels=self.input_label_vector_indices, name='softmax')
            L2 = parameters['beta_l2'] * tf.add_n([tf.nn.l2_loss(param) for param in tf.trainable_variables()])
            self.loss = tf.reduce_mean(losses, name='cross_entropy_mean_loss') + self.penalized_term + L2

        with tf.variable_scope("accuracy"):
            correct_predictions = tf.equal(self.yhat, tf.argmax(self.input_label_vector_indices, 1))
            self.accuracy = tf.reduce_mean(tf.cast(correct_predictions, 'float'), name='accuracy')

        self.define_training_procedure(parameters)

    def define_training_procedure(self, parameters):
        self.global_step = tf.Variable(0, name="global_step", trainable=False)

        if parameters['optimizer'] == 'adam':
            self.optimizer = tf.train.AdamOptimizer(parameters['learning_rate'])
        elif parameters['optimizer'] == 'sgd':
            self.optimizer = tf.train.GradientDescentOptimizer(parameters['learning_rate'])
        else:
            raise ValueError('The lr_method parameter must be either adam or sgd.')

        grads_and_vars = self.optimizer.compute_gradients(self.loss)
        if parameters['gradient_clipping_value']:
            grads_and_vars = [(tf.clip_by_value(grad, -parameters['gradient_clipping_value'], parameters['gradient_clipping_value']), var) for grad, var in grads_and_vars]

        # By defining a global_step variable and passing it to the optimizer we allow TensorFlow handle the counting of training steps for us.
        # The global step will be automatically incremented by one every time you execute train_op.
        self.train_op = self.optimizer.apply_gradients(grads_and_vars, global_step=self.global_step)

    # TODO: maybe move out of the class?
    def load_pretrained_token_embeddings(self, sess, dataset, parameters):
        if parameters['token_pretrained_embedding_filepath'] == '':
            return
        # Load embeddings
        start_time = time.time()
        print('Load token embeddings... ', end='', flush=True)
        token_to_vector = utils_nlp.load_pretrained_token_embeddings(parameters)

        initial_weights = sess.run(self.token_embedding_weights.read_value())
        number_of_loaded_word_vectors = 0
        number_of_token_original_case_found = 0
        number_of_token_lowercase_found = 0
        number_of_token_digits_replaced_with_zeros_found = 0
        number_of_token_lowercase_and_digits_replaced_with_zeros_found = 0
        for token in dataset.token_to_index.keys():
            if token in token_to_vector.keys():
                initial_weights[dataset.token_to_index[token]] = token_to_vector[token]
                number_of_token_original_case_found += 1
            elif parameters['check_for_lowercase'] and token.lower() in token_to_vector.keys():
                initial_weights[dataset.token_to_index[token]] = token_to_vector[token.lower()]
                number_of_token_lowercase_found += 1
            elif parameters['check_for_digits_replaced_with_zeros'] and re.sub('\d', '0', token) in token_to_vector.keys():
                initial_weights[dataset.token_to_index[token]] = token_to_vector[re.sub('\d', '0', token)]
                number_of_token_digits_replaced_with_zeros_found += 1
            elif parameters['check_for_lowercase'] and parameters['check_for_digits_replaced_with_zeros'] and re.sub('\d', '0', token.lower()) in token_to_vector.keys():
                initial_weights[dataset.token_to_index[token]] = token_to_vector[re.sub('\d', '0', token.lower())]
                number_of_token_lowercase_and_digits_replaced_with_zeros_found += 1
            else:
                continue
            number_of_loaded_word_vectors += 1
        elapsed_time = time.time() - start_time
        print('done ({0:.2f} seconds)'.format(elapsed_time))
        print("number_of_token_original_case_found: {0}".format(number_of_token_original_case_found))
        print("number_of_token_lowercase_found: {0}".format(number_of_token_lowercase_found))
        print("number_of_token_digits_replaced_with_zeros_found: {0}".format(number_of_token_digits_replaced_with_zeros_found))
        print("number_of_token_lowercase_and_digits_replaced_with_zeros_found: {0}".format(number_of_token_lowercase_and_digits_replaced_with_zeros_found))
        print('number_of_loaded_word_vectors: {0}'.format(number_of_loaded_word_vectors))
        print("dataset.vocabulary_size: {0}".format(dataset.vocabulary_size))
        sess.run(self.token_embedding_weights.assign(initial_weights))

    def load_embeddings_from_pretrained_model(self, sess, dataset, pretraining_dataset, pretrained_embedding_weights, embedding_type='token'):
        if embedding_type == 'token':
            embedding_weights = self.token_embedding_weights
            index_to_string = dataset.index_to_token
            pretraining_string_to_index = pretraining_dataset.token_to_index

        # Load embeddings
        start_time = time.time()
        print('Load {0} embeddings from pretrained model... '.format(embedding_type), end='', flush=True)
        initial_weights = sess.run(embedding_weights.read_value())

        if embedding_type == 'token':
            initial_weights[dataset.UNK_TOKEN_INDEX] = pretrained_embedding_weights[pretraining_dataset.UNK_TOKEN_INDEX]
            initial_weights[dataset.PADDING_TOKEN_INDEX] = pretrained_embedding_weights[pretraining_dataset.PADDING_TOKEN_INDEX]

        number_of_loaded_vectors = 1
        for index, string in index_to_string.items():
            if index == dataset.UNK_TOKEN_INDEX or index == dataset.PADDING_TOKEN_INDEX:
                continue
            if string in pretraining_string_to_index.keys():
                initial_weights[index] = pretrained_embedding_weights[pretraining_string_to_index[string]]
                number_of_loaded_vectors += 1
        elapsed_time = time.time() - start_time
        print('done ({0:.2f} seconds)'.format(elapsed_time))
        print("number_of_loaded_vectors: {0}".format(number_of_loaded_vectors))
        if embedding_type == 'token':
            print("dataset.vocabulary_size: {0}".format(dataset.vocabulary_size))

        sess.run(embedding_weights.assign(initial_weights))


def bidirectional_LSTM(input, hidden_state_dimension, initializer, batch_size, sequence_length):
    with tf.variable_scope("bidirectional_LSTM"):
        lstm_cell = {}
        initial_state = {}
        for direction in ["forward", "backward"]:
            with tf.variable_scope(direction):
                # LSTM cell
                lstm_cell[direction] = tf.contrib.rnn.CoupledInputForgetGateLSTMCell(hidden_state_dimension, forget_bias=1.0, initializer=initializer, state_is_tuple=True)
                # initial state: http://stackoverflow.com/questions/38441589/tensorflow-rnn-initial-state
                initial_cell_state = tf.get_variable("initial_cell_state", shape=[1, hidden_state_dimension], dtype=tf.float32, initializer=initializer)
                initial_output_state = tf.get_variable("initial_output_state", shape=[1, hidden_state_dimension], dtype=tf.float32, initializer=initializer)
                c_states = tf.tile(initial_cell_state, tf.stack([batch_size, 1]))
                h_states = tf.tile(initial_output_state, tf.stack([batch_size, 1]))
                initial_state[direction] = tf.contrib.rnn.LSTMStateTuple(c_states, h_states)

        # sequence_length must be provided for tf.nn.bidirectional_dynamic_rnn due to internal bug
        (outputs_forward, outputs_backward), final_states = tf.nn.bidirectional_dynamic_rnn(lstm_cell["forward"],
                                                                    lstm_cell["backward"],
                                                                    input,
                                                                    dtype=tf.float32,
                                                                    sequence_length=sequence_length,
                                                                    initial_state_fw=initial_state["forward"],
                                                                    initial_state_bw=initial_state["backward"])

        output = tf.concat([outputs_forward, outputs_backward], axis=2, name='output_sequence')

    return output

