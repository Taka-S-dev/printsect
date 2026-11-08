#ifndef NET_H
#define NET_H
#include "common.h"
status_t net_rx(const unsigned char *buf, int len);
status_t net_tx(const unsigned char *buf, int len);
#endif
