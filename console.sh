#!/bin/bash

PYTHONPATH="./test_lib/boto:$PYTHONPATH" python ./test_lib/console.py $@
