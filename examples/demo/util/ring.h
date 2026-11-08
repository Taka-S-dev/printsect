#ifndef RING_H
#define RING_H
#include "common.h"
typedef struct { int head, tail; } ring_t;
int ring_push(ring_t *r, int v);
#endif
