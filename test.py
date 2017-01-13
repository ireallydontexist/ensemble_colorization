#! /usr/bin/python

import os
import glob
import tensorflow as tf
from matplotlib import pyplot as plt
from matplotlib import colors
import numpy as np
from argparse import ArgumentParser

def parse_arguments():
    parser = ArgumentParser(description="Runs the testing phase of image "
            "recolorizationm running the trained network on the list of "
            "testing images, saving it the specified output directory.")
    parser.add_argument("image_dir", type=str, help="The directory "
        "containing the JPEG images to run testing on.")
    parser.add_argument("output_dir", type=str, help="The output directory to "
        "place the results of testing into. The results are the grayscale, "
        "test result, and original images concatenated together.")
    return parser.parse_args()

def concat_images(imga, imgb):
    """
    Combines two color image ndarrays side-by-side.
    """
    ha, wa = imga.shape[:2]
    hb, wb = imgb.shape[:2]
    max_height = np.max([ha, hb])
    total_width = wa + wb
    new_img = np.zeros(shape=(max_height, total_width, 3), dtype=np.float32)
    new_img[:ha, :wa] = imga
    new_img[:hb, wa:wa + wb] = imgb
    return new_img


def rgb2yuv(rgb):
    """
    Convert RGB image into YUV https://en.wikipedia.org/wiki/YUV
    """
    rgb2yuv_filter = tf.constant(
        [[[[0.299, -0.169, 0.499],
           [0.587, -0.331, -0.418],
            [0.114, 0.499, -0.0813]]]])
    rgb2yuv_bias = tf.constant([0., 0.5, 0.5])

    temp = tf.nn.conv2d(rgb, rgb2yuv_filter, [1, 1, 1, 1], 'SAME')
    temp = tf.nn.bias_add(temp, rgb2yuv_bias)

    return temp


def yuv2rgb(yuv):
    """
    Convert YUV image into RGB https://en.wikipedia.org/wiki/YUV
    """
    yuv = tf.mul(yuv, 255)
    yuv2rgb_filter = tf.constant(
        [[[[1., 1., 1.],
           [0., -0.34413999, 1.77199996],
            [1.40199995, -0.71414, 0.]]]])
    yuv2rgb_bias = tf.constant([-179.45599365, 135.45983887, -226.81599426])
    temp = tf.nn.conv2d(yuv, yuv2rgb_filter, [1, 1, 1, 1], 'SAME')
    temp = tf.nn.bias_add(temp, yuv2rgb_bias)
    temp = tf.maximum(temp, tf.zeros(temp.get_shape(), dtype=tf.float32))
    temp = tf.minimum(temp, tf.mul(
        tf.ones(temp.get_shape(), dtype=tf.float32), 255))
    temp = tf.div(temp, 255)
    return temp

def recombine(predictions, weights):
    """
    Combines the output images from the 3 CNN's, where each one is biased to a
    color channel, into a final output image. Recombination is done by
    pixel-wise weighting, where the pixel value for any given CNN's output is
    weighted as its relative saturation to the other two.
    """
    red_biased = predictions['red']
    green_biased = predictions['green']
    blue_biased = predictions['blue']
    blue_green_biased = predictions['blue_green']

    # Compute the pixel-wise saturation for each biased CNN output image
    red_sats = weights['red'] * colors.rgb_to_hsv(red_biased)[:,:,1]
    green_sats = weights['green'] * colors.rgb_to_hsv(green_biased)[:,:,1]
    blue_sats = weights['blue'] * colors.rgb_to_hsv(blue_biased)[:,:,1]
    blue_green_sats = (weights['blue_green'] *
            colors.rgb_to_hsv(blue_green_biased)[:,:,1])

    # Weight each CNN-bias by its relative saturations at each pixel
    total_sats = red_sats + blue_sats + green_sats + blue_green_sats
    red_weights = red_sats / total_sats
    green_weights = green_sats / total_sats
    blue_weights = blue_sats / total_sats
    blue_green_weights = blue_green_sats / total_sats

    # Rehsape the per-pixel weights so they can be multiplied with the image
    new_shape = (red_weights.shape[0], red_weights.shape[1], 1)
    red_weights = np.reshape(red_weights, new_shape)
    green_weights = np.reshape(green_weights, new_shape)
    blue_weights = np.reshape(blue_weights, new_shape)
    blue_green_weights = np.reshape(blue_green_weights, new_shape)

    # Compute the output image as the pixel-wise weighted sum of the biases
    return (red_weights * red_biased + green_weights * green_biased +
            blue_weights * blue_biased + blue_green_weights * blue_green_biased)

def main():
    args = parse_arguments()

    # Create the output directory if it does not exist
    if not os.path.exists(args.output_dir):
        os.mkdir(args.output_dir)

    # How each biased model's saturations are weighted relative to the others
    sat_weights = {
        'red': 1 / 8.0,
        'green': 7 / 32.0,
        'blue': 7 / 32.0,
        'blue_green': 7 / 16.0,
    }

    phase_train = tf.placeholder(tf.bool, name='phase_train')
    uv = tf.placeholder(tf.uint8, name='uv')

    print("Starting TF session")

    with tf.Session() as sess:
        saver = tf.train.import_meta_graph('model/model_blue.meta')

        image_paths = glob.glob(os.path.join(args.image_dir, "*.jpg"))
        print(image_paths)
        for image_path in sorted(image_paths):
            predictions = dict()
            print("\nEvaluating image '{}':".format(image_path))

            for color in ['red', 'green', 'blue', 'blue_green']:
                print("\tRunning {}-biased colornet CNN model...".format(color))
                
                print("restore color model", color)
                saver.restore(sess, 'model/model_%s' % color)

                print("get graph")
                graph = tf.get_default_graph()

                print("read file", image_path)
                contents = tf.read_file(image_path)
                uint8image = tf.image.decode_jpeg(contents, channels=3)
                resized_image = tf.div(tf.image.resize_images(uint8image, (224, 224)), 255)
                
                print("get resized image")
                img = sess.run(resized_image)

                print("get soigmoid tensor")
                pred = graph.get_tensor_by_name("colornet_1/conv2d_4/Sigmoid:0")

                grayscale = tf.image.rgb_to_grayscale(resized_image)
                grayscale = tf.reshape(grayscale, [1, 224, 224, 1])
                grayscale_rgb = tf.image.grayscale_to_rgb(grayscale)
                grayscale_yuv = rgb2yuv(grayscale_rgb)
                grayscale = tf.concat(3, [grayscale, grayscale, grayscale])

                pred_yuv = tf.concat(3, [tf.split(3, 3, grayscale_yuv)[0], pred])
                pred_rgb = yuv2rgb(pred_yuv)

                print("get grayscaled image")
                input_image = sess.run(grayscale)

                feed_dict = {phase_train : False, uv: 3, graph.get_tensor_by_name('concat:0') : input_image}

                pred_, pred_rgb_, colorimage_, grayscale_rgb_ = sess.run(
                    [pred, pred_rgb, resized_image, grayscale_rgb], feed_dict=feed_dict)

                predictions[color] = pred_rgb_[0]

            # Combine the three color-baised images into a final response
            print("\tCombining the biased CNN outputs into a final image...")
            output = recombine(predictions, sat_weights)

            # Concatenate the grayscale, result, and original images together
            output_image = concat_images(grayscale_rgb_[0], output)
            output_image = concat_images(output_image, img)

            # Save the output image to the directory with the same name
            image_name = os.path.basename(image_path)
            output_image_path = os.path.join(args.output_dir, image_name)
            print("\tSaving the evaluation to '{}'...".format(output_image_path))
            plt.imsave(output_image_path, output_image)

if __name__ == '__main__':
    main()
