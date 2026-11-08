"""What every printsect tool shares: configuration, errors, plugins, files, terminal.

Tools (deps, probe, deploy, ...) import from here and never from each other, so each
one works on its own. Joining them up is the job of the `psect` command alone.
"""
