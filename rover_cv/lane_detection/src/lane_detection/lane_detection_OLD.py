#!/usr/bin/env python
# -*- coding: utf-8 -*-
# https://github.com/paramaggarwal/CarND-LaneLines-P1/blob/master/P1.ipynb
from __future__ import print_function
from __future__ import division
import roslib
roslib.load_manifest('formulapi_sitl')
import sys
import traceback
import rospy
import cv2
import numpy as np
import math
import logging
import socket
import threading
import time
import datetime
import lane_detection_module as ld
from std_msgs.msg import String
from sensor_msgs.msg import Image
from cv_bridge import CvBridge, CvBridgeError
from geometry_msgs.msg import Twist

class lane_detection(object):
    def __init__(self):
            
      """ROS Subscriptions """
      self.image_pub = rospy.Publisher("/image_converter/output_video",Image, queue_size=10)
      self.image_sub = rospy.Subscriber("/cam/camera_/image_raw",Image,self.cvt_image) 
      self.cmdVelocityPub = rospy.Publisher('/platform_control/cmd_vel', Twist, queue_size=10)

      """ Variables """
      self.bridge = CvBridge()
      self.latestImage = None
      self.outputImage = None
      self.process = False

      self.kernel_size_blur = 50
      self.processedImage = None

      self.cmdvel = Twist()
      self.last_time = rospy.Time()
      self.sim_time = rospy.Time()
      self.dt = 0
      self.pos = 0
      self.position_er = 0
      self.position_er_last = 0
      self.cp = 0
      self.vel_er = 0
      self.cd = 0
      self.Kp = .0025
      self.Kd = .0003      

      self.avgLeft = (0, 0, 0, 0)
      self.avgRight = (0, 0, 0, 0)
      self.intersectionPoint = (0,  0)
      
    def limit(self, input, min, max):
	if input < min:
		input = min
	if input > max:
		input = max
	return input

    def AdjustMotorSpeed(self, image,  pos):
        
        if (math.isnan(pos[0])== False and math.isnan(pos[0]) == False):
            
             self.cmdvel.linear.x = 0.2
    
             self.sim_time = rospy.Time.now()
             self.dt = (self.sim_time - self.last_time).to_sec();             

             self.position_er = image.shape[1]/2 - pos[0]
             self.cp = self.position_er * self.Kp 
             self.vel_er = (self.position_er - self.position_er_last) * self.dt
             self.cd = self.vel_er * self.Kd

             self.cmdvel.angular.z = self.cp - self.cd
             self.cmdvel.angular.z = ld.limit(self.cmdvel.angular.z, -1, 1)
           
             self.cmdVelocityPub.publish(self.cmdvel)

             self.position_er_last = self.position_er
             self.last_time = self.sim_time
        else:
             self.cmdvel.linear.x = 0.0
             self.cmdvel.angular.z = 0.0
             self.cmdVelocityPub.publish(self.cmdvel)

    def cvt_image(self,data):  
      try:
        self.latestImage = self.bridge.imgmsg_to_cv2(data, "bgr8")	
      except CvBridgeError as e:
        print(e)
      if self.process != True:
          self.process = True    


     # line segment a given by endpoints a1, a2
     # line segment b given by endpoints b1, b2
     # return 
    def perp(self,  a ) :
         b = np.empty_like(a)
         b[0] = -a[1]
         b[1] = a[0]
         return b
    def seg_intersect(self, a1,a2, b1,b2):
         da = a2-a1
         db = b2-b1
         dp = a1-b1
         dap = self.perp(da)
         denom = np.dot( dap, db)
         num = np.dot( dap, dp )
         return (num / denom.astype(float))*db + b1
    
    def movingAverage(self, avg, new_sample, N=15):
         if (avg == 0):
             return new_sample
         avg -= avg / N;
         avg += new_sample / N;
         return avg;
    
    def process_image(self, image):
      
      #Gaussian Blur
      """Applies a Gaussian Noise kernel"""
      blurredImage = cv2.GaussianBlur(image, (11,11), 0) 

      #Canny edge detection
      """Applies the Canny transform"""
      edgesImage = cv2.Canny(blurredImage, 40, 50) 
   
      #Define region of interest for cropping
      height = image.shape[0]
      width = image.shape[1]
      
      # For detecting center lines
      """
      vertices = np.array( [[
                [3*width/5, 3*height/5],
                [2*width/5, 3*height/5],
                [3*width/5, height],
                [2*width/5, height]
            ]], dtype=np.int32 )
      """
      
      # For detecting lane lines
      vertices = np.array( [[
                [4*width/4, 3*height/5],
                [0*width/4, 3*height/5],
                [10, height],
                [width-10, height]
            ]], dtype=np.int32 )

      #defining a blank mask to start with
      mask = np.zeros_like(edgesImage)   
      
      #defining a 3 channel or 1 channel color to fill the mask with depending on the input image
      if len(edgesImage.shape) > 2:
          channel_count = edgesImage.shape[2]  # i.e. 3 or 4 depending on your image
          ignore_mask_color = (255,) * channel_count
      else:
          ignore_mask_color = 255
      
      #filling pixels inside the polygon defined by "vertices" with the fill color    
      cv2.fillPoly(mask, vertices, ignore_mask_color)
    
      #returning the image only where mask pixels are nonzero
      maskedImage = cv2.bitwise_and(edgesImage, mask)

      """
      `img` should be the output of a Canny transform.
        
      Returns an image with hough lines drawn.
      """
      rho = 1
      theta = np.pi/180
      threshold = 100
      min_line_len = 30
      max_line_gap = 50
      lines = (0, 0, 0, 0)

      lines = cv2.HoughLinesP(maskedImage, rho, theta, threshold, np.array([]), min_line_len, max_line_gap)
      #line_img = np.zeros(image.shape, dtype=np.uint8)
      line_img = np.zeros_like(image)
     
     # state variables to keep track of most dominant segment
      largestLeftLineSize = 0
      largestRightLineSize = 0
      largestLeftLine = (0,0,0,0)
      largestRightLine = (0,0,0,0)    
      
      
      if lines is None:
        avgx1, avgy1, avgx2, avgy2 = self.avgLeft
        cv2.line(image, (int(avgx1), int(avgy1)), (int(avgx2), int(avgy2)), [255,255,255], 12) #draw left line
        avgx1, avgy1, avgx2, avgy2 = self.avgRight
        cv2.line(image, (int(avgx1), int(avgy1)), (int(avgx2), int(avgy2)), [255,255,255], 12) #draw right line
        #return
      if lines is not None:
          for line in lines:
            for x1,y1,x2,y2 in line:
                size = float(math.hypot(x2 - x1, y2 - y1))
                slope = float((y2-y1)/(x2-x1))                
                # Filter slope based on incline and
                # find the most dominent segment based on length
                if (slope > 0.1): #right
                    if (size > largestRightLineSize):
                        largestRightLine = (x1, y1, x2, y2)                    
                    #cv2.line(line_img, (x1, y1), (x2, y2), (255,0, 0),2) #Show every line found
                elif (slope < -0.1): #left
                    if (size > largestLeftLineSize):
                        largestLeftLine = (x1, y1, x2, y2)
                    #cv2.line(line_img, (x1, y1), (x2, y2), (255,0,0),2)    #Show every line found
                    
      # Show largest line found on either side
      #cv2.line(image, (largestRightLine[0], largestRightLine[1]), (largestRightLine[2], largestRightLine[3]), (255,0,0),8)
      #cv2.line(image, (largestLeftLine[0], largestLeftLine[1]), (largestLeftLine[2], largestLeftLine[3]), (255,0,0),8) 
 
      # Define an imaginary horizontal line in the center of the screen
      # and at the bottom of the image, to extrapolate determined segment
      imgHeight, imgWidth = (line_img.shape[0], line_img.shape[1])
      upLinePoint1 = np.array( [0, int(imgHeight - (imgHeight/2))] )
      upLinePoint2 = np.array( [int(imgWidth), int(imgHeight - (imgHeight/2))] )
      downLinePoint1 = np.array( [0, int(imgHeight)] )
      downLinePoint2 = np.array( [int(imgWidth), int(imgHeight)] )
      
      # Find the intersection of dominant lane with an imaginary horizontal line
      # in the middle of the image and at the bottom of the image.
      p3 = np.array( [largestLeftLine[0], largestLeftLine[1]] )
      p4 = np.array( [largestLeftLine[2], largestLeftLine[3]] )
      upLeftPoint = self.seg_intersect(upLinePoint1,upLinePoint2, p3,p4)
      downLeftPoint = self.seg_intersect(downLinePoint1,downLinePoint2, p3,p4)
      if (math.isnan(upLeftPoint[0]) or math.isnan(downLeftPoint[0])):
         avgx1, avgy1, avgx2, avgy2 = self.avgLeft
         #cv2.line(image, (int(avgx1), int(avgy1)), (int(avgx2), int(avgy2)), [255,255,255], 12) #draw left line
         avgx1, avgy1, avgx2, avgy2 = self.avgRight
         #cv2.line(image, (int(avgx1), int(avgy1)), (int(avgx2), int(avgy2)), [255,255,255], 12) #draw right line
      else:
         cv2.line(line_img, (int(upLeftPoint[0]), int(upLeftPoint[1])), (int(downLeftPoint[0]), int(downLeftPoint[1])), [0, 0, 255], 8) #draw left line
          
      # Calculate the average position of detected left lane over multiple video frames and draw
      if (math.isnan(upLeftPoint[0])== False and math.isnan(downLeftPoint[0]) == False):
         avgx1, avgy1, avgx2, avgy2 = self.avgLeft
         self.avgLeft = (self.movingAverage(avgx1, upLeftPoint[0]), self.movingAverage(avgy1, upLeftPoint[1]), self.movingAverage(avgx2, downLeftPoint[0]), self.movingAverage(avgy2, downLeftPoint[1]))
         avgx1, avgy1, avgx2, avgy2 = self.avgLeft
         cv2.line(image, (int(avgx1), int(avgy1)), (int(avgx2), int(avgy2)), [255,255,255], 12) #draw left line
      

      # Find the intersection of dominant lane with an imaginary horizontal line
      # in the middle of the image and at the bottom of the image.
      p5 = np.array( [largestRightLine[0], largestRightLine[1]] )
      p6 = np.array( [largestRightLine[2], largestRightLine[3]] )
      upRightPoint = self.seg_intersect(upLinePoint1,upLinePoint2, p5,p6)
      downRightPoint = self.seg_intersect(downLinePoint1,downLinePoint2, p5,p6)
      if (math.isnan(upRightPoint[0]) or math.isnan(downRightPoint[0])):
         avgx1, avgy1, avgx2, avgy2 = self.avgLeft
         #cv2.line(image, (int(avgx1), int(avgy1)), (int(avgx2), int(avgy2)), [255,255,255], 12) #draw left line
         avgx1, avgy1, avgx2, avgy2 = self.avgRight
         #cv2.line(image, (int(avgx1), int(avgy1)), (int(avgx2), int(avgy2)), [255,255,255], 12) #draw right line
      else:
         cv2.line(line_img, (int(upRightPoint[0]), int(upRightPoint[1])), (int(downRightPoint[0]), int(downRightPoint[1])), [0, 0, 255], 8) #draw left line
      
      # Calculate the average position of detected right lane over multiple video frames and draw
      if (math.isnan(upRightPoint[0])== False and math.isnan(downRightPoint[0]) == False):
         avgx1, avgy1, avgx2, avgy2 = self.avgRight
         self.avgRight = (self.movingAverage(avgx1, upRightPoint[0]), self.movingAverage(avgy1, upRightPoint[1]), self.movingAverage(avgx2, downRightPoint[0]), self.movingAverage(avgy2, downRightPoint[1]))
         avgx1, avgy1, avgx2, avgy2 = self.avgRight         
         cv2.line(image, (int(avgx1), int(avgy1)), (int(avgx2), int(avgy2)), [255,255,255], 12) #draw right line
      
      # Calculate intersection of detected lane lines
      al1 = np.array( [self.avgLeft[0],  self.avgLeft[1]] )
      al2 = np.array( [self.avgLeft[2],  self.avgLeft[3]] )
      ar1 = np.array( [self.avgRight[0],  self.avgRight[1]] )
      ar2 = np.array( [self.avgRight[2],  self.avgRight[3]] )
      self.intersectionPoint = self.seg_intersect(al1, al2, ar1, ar2)
      print(self.intersectionPoint)
      if (math.isnan(self.intersectionPoint[0])== False and math.isnan(self.intersectionPoint[0]) == False): 
         cv2.circle(image, (int(self.intersectionPoint[0]), int(self.intersectionPoint[1])), 12, (0, 255, 0), -1)
      
      self.processedImage = image
      

 
    def run(self):
     
     while True:
         # Only run loop if we have an image
         if self.process:
             self.process_image(self.latestImage)	# Lane Detection Function

             self.AdjustMotorSpeed(self.latestImage, self.intersectionPoint)	# Compute Motor Commands From Image Output

             # Publish Processed Image
             cvImage = self.processedImage
             
             try:
                 imgmsg = self.bridge.cv2_to_imgmsg(cvImage, "bgr8") 
                 #imgmsg = self.bridge.cv2_to_imgmsg(cvImage, "bgr8")  #"mono8" "bgr8"
                 self.image_pub.publish(imgmsg)
             except CvBridgeError as e:
                 print(e)


def main(args):

  rospy.init_node('lane_detection', anonymous=True)

  ld = lane_detection() 

  ld.run() 


  try:
    rospy.spin()
  except KeyboardInterrupt:
    print("Shutting down")
  cv2.destroyAllWindows()

if __name__ == '__main__':
    main(sys.argv)
