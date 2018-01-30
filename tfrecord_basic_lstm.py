'''
Created on Jan 30, 2018
	@author: Varela
	
	Using tensorflow's Coordinator/Queue
	Using batching

'''
import numpy as np 
import tensorflow as tf 

from datasets.data_embed import embed_input_lazyload, embed_output_lazyload  

TARGET_PATH='datasets/training/pre/00/'
tfrecords_filename= TARGET_PATH + 'devel.tfrecords'


def read_and_decode(filename_queue):
	reader= tf.TFRecordReader()
	_, serialized_example= reader.read(filename_queue)

	context_features, sequence_features= tf.parse_single_sequence_example(
		serialized_example,
		context_features={
			'T': tf.FixedLenFeature([], tf.int64)			
		},
		sequence_features={
			'PRED':tf.VarLenFeature(tf.int64),			
			'LEMMA': tf.VarLenFeature(tf.int64),
			'M_R':tf.VarLenFeature(tf.int64),
			'targets':tf.VarLenFeature(tf.int64)		
		}
	)

	# INT<1>
	length= 	 	 tf.cast(context_features['T'], tf.int32)	
	

	# INT<1>
	predicate= 	 tf.sparse_tensor_to_dense(sequence_features['PRED'])	
	#lemma<TIME,>

	lemma= 	  tf.sparse_tensor_to_dense(sequence_features['LEMMA'])	
	#mr<TIME,> of zeros and ones	
	mr= 	 		tf.cast( tf.sparse_tensor_to_dense(sequence_features['M_R']), dtype=tf.float32)	
	#target<TIME,> of integers
	# targets_sparse= sequence_features['targets']
	targets= 	tf.sparse_tensor_to_dense(sequence_features['targets'])	


	return length, predicate, lemma, mr, targets 


def process_example(length,  idx_pred, idx_lemma,  mr, targets, embeddings, klass_ind):
	LEMMA  = tf.nn.embedding_lookup(embeddings, idx_lemma)
	#PRED<EMBEDDING_SIZE,> --> <1,EMBEDDING_SIZE> 
	PRED   = tf.nn.embedding_lookup(embeddings, idx_pred)
	
	Y= tf.squeeze(tf.nn.embedding_lookup(klass_ind, targets),1 )

	M_R= tf.expand_dims(mr, 2)

	X= tf.squeeze( tf.concat((LEMMA, PRED, M_R), 2),1) 
	return X, Y, length

# https://www.tensorflow.org/api_guides/python/reading_data#Preloaded_data
def input_pipeline(filenames, batch_size,  num_epochs, embeddings, klass_ind):
	filename_queue = tf.train.string_input_producer(filenames, num_epochs=num_epochs, shuffle=True)

	length,  idx_pred, idx_lemma,  mr, targets= read_and_decode(filename_queue)	

	X, Y, l  = process_example(length,  idx_pred, idx_lemma,  mr, targets, embeddings, klass_ind)

	min_after_dequeue = 10000
	capacity = min_after_dequeue + 3 * batch_size

	# https://www.tensorflow.org/api_docs/python/tf/train/batch
	example_batch, target_batch, length_batch=tf.train.batch(
		[X, Y, l], 
		batch_size=batch_size, 
		capacity=capacity, 
		dynamic_pad=True		
	)
	return example_batch, target_batch, length_batch


def forward(X, Wo, bo, sequence_length, hidden_size):		
	'''
		Computes forward propagation thru cell
		IN
			X  tf.Tensor(tf.float32) <batch_size,max_time, FEATURE_SIZE>: Batch input

			Wo tf.Tensor(tf.float32) <hidden_size, klass_size>: Batch input

			bo tf.Tensor(tf.float32) <klass_size>: Batch input

		OUT
			Yhat tf.Tensor<batch_size, FEATURE_SIZE>: Batch output

	'''
	basic_cell = tf.nn.rnn_cell.BasicLSTMCell(hidden_size, forget_bias=1.0)

	# 'outputs' is a tensor of shape [batch_size, max_time, cell_state_size]
	outputs, states= tf.nn.dynamic_rnn(
			cell=basic_cell, 
			inputs=X, 			
			sequence_length=sequence_length,
			dtype=tf.float32,
			time_major=False
		)

	return tf.matmul(outputs, tf.stack([Wo]*200)) + bo


if __name__== '__main__':	
	EMBEDDING_SIZE=50 
	KLASS_SIZE=60
	
	FEATURE_SIZE=2*EMBEDDING_SIZE+1
	lr=1e-3
	BATCH_SIZE=200	
	N_EPOCHS=100
	HIDDEN_SIZE=128
	DISPLAY_STEP=100

	word2idx,  np_embeddings= embed_input_lazyload()		
	klass2idx, np_klassind= embed_output_lazyload()		

	embeddings= tf.constant(np_embeddings.tolist(), shape=np_embeddings.shape, dtype=tf.float32)
	klass_ind= tf.constant(np_klassind.tolist(),   shape=np_klassind.shape, dtype=tf.int32)

	inputs, targets, sequence_length = input_pipeline([tfrecords_filename], BATCH_SIZE, N_EPOCHS, embeddings, klass_ind)
	
	#define variables / placeholders
	Wo = tf.Variable(tf.random_normal([HIDDEN_SIZE, KLASS_SIZE], name='Wo')) 
	bo = tf.Variable(tf.random_normal([KLASS_SIZE], name='bo')) 
	

	predict_op= forward(inputs, Wo, bo, sequence_length, HIDDEN_SIZE)
	cost_op= tf.reduce_mean(tf.nn.softmax_cross_entropy_with_logits(logits=predict_op, labels=targets))
	optimizer_op = tf.train.AdamOptimizer(learning_rate=lr).minimize(cost_op)

	#Evaluation
	success_count_op= tf.equal(tf.argmax(predict_op,1), tf.argmax(targets,1))
	accuracy_op = tf.reduce_mean(tf.cast(success_count_op, tf.float32))	

	#Initialization 
	#must happen after every variable has been defined
	init_op = tf.group( 
		tf.global_variables_initializer(),
		tf.local_variables_initializer()
	)
	with tf.Session() as session: 
		session.run(init_op) 
		coord= tf.train.Coordinator()
		threads= tf.train.start_queue_runners(coord=coord)
		step=0
		total_loss=0
		total_acc=0
		try:
			while not coord.should_stop():				
				_,loss, acc = session.run(
					[optimizer_op, cost_op, accuracy_op]
				)
				total_loss+=loss 
				total_acc+= acc

				if step % DISPLAY_STEP ==0:					
					print('avg acc {:.2f}%'.format(100*total_acc/DISPLAY_STEP), 'avg cost {:.6f}'.format(total_loss/DISPLAY_STEP))
					total_loss=0
					total_acc=0
				step+=1
				
		except tf.errors.OutOfRangeError:
			print('Done training -- epoch limit reached')

		finally:
			#When done, ask threads to stop
			coord.request_stop()
			
		coord.request_stop()
		coord.join(threads)

