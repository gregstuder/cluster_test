#!/bin/bash

PYTHONPATH="./test_lib/boto:./test_lib/pychef:./test_lib/base_remote_resources:$PYTHONPATH" python ./test_lib/console.py $@
