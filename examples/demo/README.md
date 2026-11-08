# demo

A made-up project for trying printsect: two loadable modules built from three
archives with sub-makes. The tools in config.mk do not exist; `psect deps` only asks
make what it would run (`make -n`), so nothing is built.

    psect deps
    psect deps tree
    psect deps which util/ring.h
