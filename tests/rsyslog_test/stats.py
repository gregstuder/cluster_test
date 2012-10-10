# Statistics script

print "Hello World!"

# Setup X11 Forwardable matplotlib and pyplot
import matplotlib
matplotlib.use('GTK')
from matplotlib import pyplot

# Import numpy
import numpy

# PyMongo
import pymongo
conn = pymongo.Connection('localhost:27017')

# Import our stats modules shared between tests
import sys
sys.path.append('./base_remote_resources/stats')
import tengenstats

# Import test-local stuff in remote_resources
sys.path.append('./remote_resources')
import mymodule

# Everything else
import os

count = 0
total = 0

with open('/var/log/syslog', 'r') as f:
    for line in f:
        if 'mongos' in line: count = count + 1
        total = total + 1

print "Count of mongos log entries is ", count, " out of ", total

x = [1, 2, 3, 4]
y = [3, 4, 8, 20]

pyplot.plot(x, y)
pyplot.show()



