"""
Setup Module
Compiles the C functions
"""
import sys
import os.path
from setuptools import setup, find_packages

this = os.path.dirname(__file__)
that = os.path.join(this, "pyreduce")
sys.path.append(that)
try:
    from clib import build_extract
except ModuleNotFoundError:
    # Wait for pip to install CFFI first
    print("Install CFFI")
    pass


# from pyreduce.clib import build_cluster
# build_cluster.build()

# from .pyreduce.clib import build_extract

# build_extract.build()

with open("README.md", "r") as fh:
    long_description = fh.read()

setup(
    name="pyreduce-astro",
    version="0.02",
    author="Ansgar Wehrhahn",
    author_email="ansgar.wehrhahn@physics.uu.se",
    description="A data reduction package for echelle spectrographs",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/AWehrhahn/PyReduce",
    packages=find_packages(),
    include_package_data=True,
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
        "Operating System :: POSIX :: Linux",
    ],
    # setup_requires=["cffi>=1.0.0"],
    cffi_modules=[
        "pyreduce/clib/build_extract.py:ffibuilder_vertical",
        "pyreduce/clib/build_extract.py:ffibuilder_curved",
    ],
    install_requires=[
        "cffi>=1.0.0",
        "numpy",
        "scipy",
        "astropy",
        "matplotlib",
        "scikit-image",
        "python-dateutil",
        "wget",
        "joblib",
        "jsonschema>=3.0.1",
        "pyreduce-astro==0.2",
    ],
)
