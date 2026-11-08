#include "disk.h"
#include "log.h"

status_t blk_read(int lba, void *buf)
{
    (void)buf;
    if (lba < 0) {
        log_put("blk: bad lba");
        return -1;
    }
    return 0;
}
