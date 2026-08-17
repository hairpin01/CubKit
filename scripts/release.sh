#!/bin/bash

rm -fr dist/
proxychains4 python3 -m build
twine upload dist/*
