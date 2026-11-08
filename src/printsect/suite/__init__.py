"""Joining the tools up: what only makes sense with several of them together.

Unlike a tool, this package may use every tool; no tool may use it. The `psect`
command (printsect/cli.py) is its front.

    run.py    one turn of the debugging loop (build, deploy, reproduce, read, count)
    build.py  the build for the probes in: rules filled in from the graph, in order
    doctor.py `psect doctor` and `psect check`: is the project set up for a run
    approve.py  commands in the settings run for an agent only once a person approved them
    mcp.py    `psect mcp`: the tools for an AI agent, over MCP
    guide.md  how an agent finds a bug with printsect (sent as the MCP instructions)
    guide_setup.md  how an agent sets a project up (sent with it)
"""
