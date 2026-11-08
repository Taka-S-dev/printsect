# Two loadable modules built from three archives.
include config.mk


MODULES = util net disk
OUT     = build/out

.PHONY: all $(MODULES) clean
all: $(OUT)/netdrv.out $(OUT)/diskdrv.out

$(MODULES):
	$(MAKE) -C $@

# netdrv.out = net + util, partly linked, then the constructor table is added.
$(OUT)/netdrv.out: net util
	@mkdir -p $(OUT)
	$(LD) -r -o build/obj/netdrv_part.o -Lbuild/lib -lnet -lutil
	$(CC) -c gen/ctors.c -o build/obj/netdrv_ctors.o
	$(LD) -r -o $@ build/obj/netdrv_part.o build/obj/netdrv_ctors.o

$(OUT)/diskdrv.out: disk util
	@mkdir -p $(OUT)
	$(LD) -r -o $@ -Lbuild/lib -ldisk build/lib/libutil.a

clean:
	rm -rf build
