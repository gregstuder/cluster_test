#!/bin/bash

PYTHONPATH="./test_lib/boto:./test_lib/pychef:$PYTHONPATH" python ./test_lib/console.py $@
