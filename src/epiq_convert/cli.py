"""Console entry points."""
import runpy
import sys


def convert():
    """`epiq-convert`: open the app."""
    from .app import main
    sys.exit(main())


def combine():
    """`epiq-combine A.nxs B.nxs [C.nxs] -o combined.nxs`: run the combine
    script without the app."""
    runpy.run_module("epiq_convert.combine_rsm", run_name="__main__")
