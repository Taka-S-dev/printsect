# A build of its own

A made-up project built by a script from a list of targets, rather than by one Makefile.
`to_graph.py` reads `targets.csv` and writes what printsect needs; nothing about this build is
in printsect itself. Everything here is invented.

```sh
python to_graph.py build.json
psect-deps --from-graph build.json --root .
psect-deps impact modules/beta/table.def --root .
```

The last line follows a change through the generated `table.c` and the library to the program
and where it is deployed. Write the file again when `targets.csv` or `build.sh` changes.
