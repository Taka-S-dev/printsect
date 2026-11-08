# Shared settings. The tools are fictitious: `make -n` only prints the commands.
CC      = xcc
AR      = xar
LD      = xld
ROOT   := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
LIBDIR  = $(ROOT)/build/lib
CFLAGS  = -O2 -Wall -I$(ROOT)/inc
