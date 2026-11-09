#!/bin/sh
# A made-up build that no Makefile describes as a whole: a generated table, then one
# make for each module, then the program. It stands in for a script of your own.
set -e
mkdir -p build
sed 's/^/int value_/; s/$/ = 0;/' modules/beta/table.def > modules/beta/table.c
make -C modules/alpha
make -C modules/beta
make -f app/main.mak
