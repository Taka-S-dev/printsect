#include "ring.h"

int ring_push(ring_t *r, int v)
{
    r->head = (r->head + 1) % 16;
    return v;
}
