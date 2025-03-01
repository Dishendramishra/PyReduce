"""
Collection of various useful and/or reoccuring functions across PyReduce
"""

import argparse
import logging
import os
from itertools import product

import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D
import numpy as np
from astropy.io import fits
from astropy import time, coordinates as coord, units as u
import scipy.constants
import scipy.interpolate
from scipy.linalg import solve, solve_banded, lstsq
from scipy.ndimage.filters import median_filter
from scipy.optimize import curve_fit, least_squares

try:
    import git

    hasGit = True
except ImportError:
    hasGit = False


from .clipnflip import clipnflip
from .instruments.instrument_info import modeinfo


def checkGitRepo(remote_name="origin"):
    # TODO currently this runs everytime PyReduce is called
    if not hasGit:
        print("Install GitPython to check the git repository for updates")
        return

    try:
        repo = git.Repo()
        # branch = repo.active_branch
        if len(repo.remotes) == 0:
            print("No remotes found in Git repository")
            return
        if len(repo.remotes) == 1:
            remote = repo.remotes[0]
            remote_name = remote.name
        else:
            remote = repo.remotes[remote_name]
        info = remote.fetch()
        remote_commit = info[0].commit
        current_commit = repo.commit()
    except Exception:
        print("Couldn't read remote Git repository %s", remote_name)

    if remote_commit.authored_date > current_commit.authored_date:
        print("A newer commit is available from remote Git %s", remote_name)
        # while True:
        #     install = input("Install it? [Y/n]")
        #     if install.lower() in ["", "y", "yes", "1"]:
        #         install = True
        #         break
        #     elif install.lower in ["n", "no", "0"]:
        #         install = False
        #         break

        # if install:
        #     print("Pulling newest commit")
        #     remote.pull()
        #     repo.status()


def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description="General REDUCE script")
    parser.add_argument("-b", "--bias", action="store_true", help="Create master bias")
    parser.add_argument("-f", "--flat", action="store_true", help="Create master flat")
    parser.add_argument("-o", "--orders", action="store_true", help="Trace orders")
    parser.add_argument("-n", "--norm_flat", action="store_true", help="Normalize flat")
    parser.add_argument(
        "-w", "--wavecal", action="store_true", help="Prepare wavelength calibration"
    )
    parser.add_argument(
        "-s", "--science", action="store_true", help="Extract science spectrum"
    )
    parser.add_argument(
        "-c", "--continuum", action="store_true", help="Normalize continuum"
    )

    parser.add_argument("instrument", type=str, help="instrument used")
    parser.add_argument("target", type=str, help="target star")

    args = parser.parse_args()
    instrument = args.instrument.upper()
    target = args.target.upper()

    steps_to_take = {
        "bias": args.bias,
        "flat": args.flat,
        "orders": args.orders,
        "norm_flat": args.norm_flat,
        "wavecal": args.wavecal,
        "science": args.science,
        "continuum": args.continuum,
    }
    steps_to_take = [k for k, v in steps_to_take.items() if v]

    # if no steps are specified use all
    if len(steps_to_take) == 0:
        steps_to_take = "all"

    return {"instrument": instrument, "target": target, "steps": steps_to_take}


def in_ipynb():
    try:
        cfg = get_ipython().config
        if cfg["IPKernelApp"]["parent_appname"] == "ipython-notebook":
            return True
        else:
            return False
    except NameError:
        return False


def start_logging(log_file="log.log"):
    """Start logging to log file and command line

    Parameters
    ----------
    log_file : str, optional
        name of the logging file (default: "log.log")
    """

    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    # Remove existing File handles
    hasStream = False
    for h in list(logger.handlers):
        if isinstance(h, logging.FileHandler):
            logger.removeHandler(h)
        if isinstance(h, logging.StreamHandler):
            hasStream = True

    # Command Line output
    # only if not running in notebook
    if not hasStream:
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch_formatter = logging.Formatter("%(levelname)s - %(message)s")
        ch.setFormatter(ch_formatter)
        logger.addHandler(ch)

    # Log file settings
    if log_file is not None:
        log_dir = os.path.dirname(log_file)
        if log_dir != "" and not os.path.exists(log_dir):
            os.makedirs(log_dir)
        file = logging.FileHandler(log_file)
        file.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        file.setFormatter(file_formatter)
        logger.addHandler(file)

    logging.captureWarnings(True)

    logging.debug("----------------------")


def load_fits(
    fname, instrument, mode, extension, mask=None, header_only=False, dtype=None
):
    """
    load fits file, REDUCE style

    primary and extension header are combined
    modeinfo is applied to header
    data is clipnflipped
    mask is applied

    Parameters
    ----------
    fname : str
        filename
    instrument : str
        name of the instrument
    mode : str
        instrument mode
    extension : int
        data extension of the FITS file to load
    mask : array, optional
        mask to add to the data
    header_only : bool, optional
        only load the header, not the data
    dtype : str, optional
        numpy datatype to convert the read data to

    Returns
    --------
    data : masked_array
        FITS data, clipped and flipped, and with mask
    header : fits.header
        FITS header (Primary and Extension + Modeinfo)

    ONLY the header is returned if header_only is True 
    """
    hdu = fits.open(fname)
    header = hdu[extension].header
    header.extend(hdu[0].header, strip=False)
    header = modeinfo(header, instrument, mode)

    if header_only:
        hdu.close()
        return header

    data = clipnflip(hdu[extension].data, header)

    if dtype is not None:
        data = data.astype(dtype)

    data = np.ma.masked_array(data, mask=mask)

    hdu.close()
    return data, header


def swap_extension(fname, ext, path=None):
    """ exchange the extension of the given file with a new one """
    if path is None:
        path = os.path.dirname(fname)
    nameout = os.path.basename(fname)
    if nameout[-3:] == ".gz":
        nameout = nameout[:-3]
    nameout = nameout.rsplit(".", 1)[0]
    nameout = os.path.join(path, nameout + ext)
    return nameout


def find_first_index(arr, value):
    """ find the first element equal to value in the array arr """
    try:
        return next(i for i, v in enumerate(arr) if v == value)
    except StopIteration:
        raise Exception("Value %s not found" % value)


def interpolate_masked(masked):
    """ Interpolate masked values, from non masked values

    Parameters
    ----------
    masked : masked_array
        masked array to interpolate on

    Returns
    -------
    interpolated : array
        interpolated non masked array
    """

    mask = np.ma.getmaskarray(masked)
    idx = np.nonzero(~mask)[0]
    interpol = np.interp(np.arange(len(masked)), idx, masked[idx])
    return interpol


def cutout_image(img, ymin, ymax, xmin, xmax):
    """Cut a section of an image out

    Parameters
    ----------
    img : array
        image
    ymin : array[ncol](int)
        lower y value
    ymax : array[ncol](int)
        upper y value
    xmin : int
        lower x value
    xmax : int
        upper x value

    Returns
    -------
    cutout : array[height, ncol]
        selection of the image
    """

    cutout = np.zeros((ymax[0] - ymin[0] + 1, xmax - xmin), dtype=img.dtype)
    for i, x in enumerate(range(xmin, xmax)):
        cutout[:, i] = img[ymin[x] : ymax[x] + 1, x]
    return cutout


def make_index(ymin, ymax, xmin, xmax, zero=0):
    """ Create an index (numpy style) that will select part of an image with changing position but fixed height

    The user is responsible for making sure the height is constant, otherwise it will still work, but the subsection will not have the desired format

    Parameters
    ----------
    ymin : array[ncol](int)
        lower y border
    ymax : array[ncol](int)
        upper y border
    xmin : int
        leftmost column
    xmax : int
        rightmost colum
    zero : bool, optional
        if True count y array from 0 instead of xmin (default: False)

    Returns
    -------
    index : tuple(array[height, width], array[height, width])
        numpy index for the selection of a subsection of an image
    """

    # TODO
    # Define the indices for the pixels between two y arrays, e.g. pixels in an order
    # in x: the rows between ymin and ymax
    # in y: the column, but n times to match the x index
    ymin = np.asarray(ymin, dtype=int)
    ymax = np.asarray(ymax, dtype=int)
    xmin = int(xmin)
    xmax = int(xmax)

    if zero:
        zero = xmin

    index_x = np.array(
        [np.arange(ymin[col], ymax[col] + 1) for col in range(xmin - zero, xmax - zero)]
    )
    index_y = np.array(
        [
            np.full(ymax[col] - ymin[col] + 1, col)
            for col in range(xmin - zero, xmax - zero)
        ]
    )
    index = index_x.T, index_y.T + zero

    return index

def gridsearch(func, grid, args=(), kwargs={}):
    matrix = np.zeros(grid.shape[:-1])

    for idx in np.ndindex(grid.shape[:-1]):
        value = grid[idx]
        print(f"Value: {value}")
        try:
            result = func(value, *args, **kwargs)
            print(f"Success: {result}")
        except Exception as e:
            result = np.nan
            print(f"Failed: {e}")
        finally:
            matrix[idx] = result

    return matrix

def gaussfit(x, y):
    """
    Fit a simple gaussian to data

    gauss(x, a, mu, sigma) = a * exp(-z**2/2)
    with z = (x - mu) / sigma

    Parameters
    ----------
    x : array(float)
        x values
    y : array(float)
        y values
    Returns
    -------
    gauss(x), parameters
        fitted values for x, fit paramters (a, mu, sigma)
    """

    gauss = lambda x, A0, A1, A2: A0 * np.exp(-((x - A1) / A2) ** 2 / 2)
    popt, _ = curve_fit(gauss, x, y, p0=[max(y), 0, 1])
    return gauss(x, *popt), popt


def gaussfit2(x, y):
    """Fit a gaussian(normal) curve to data x, y

    gauss = A * exp(-(x-mu)**2/(2*sig**2)) + offset

    Parameters
    ----------
    x : array[n]
        x values
    y : array[n]
        y values

    Returns
    -------
    popt : array[4]
        coefficients of the gaussian: A, mu, sigma**2, offset
    """

    gauss = gaussval2

    x = np.ma.compressed(x)
    y = np.ma.compressed(y)

    if len(x) == 0 or len(y) == 0:
        raise ValueError("All values masked")

    if len(x) != len(y):
        raise ValueError("The masks of x and y are different")

    # Find the peak in the center of the image
    weights = np.ones(len(y), dtype=y.dtype)
    midpoint = len(y) // 2
    weights[:midpoint] = np.linspace(0, 1, midpoint, dtype=weights.dtype)
    weights[midpoint:] = np.linspace(1, 0, len(y) - midpoint, dtype=weights.dtype)

    i = np.argmax(y * weights)
    p0 = [y[i], x[i], 1]
    with np.warnings.catch_warnings():
        np.warnings.simplefilter("ignore")
        res = least_squares(
            lambda c: gauss(x, *c, np.ma.min(y)) - y,
            p0,
            loss="soft_l1",
            bounds=(
                [min(np.ma.mean(y), y[i]), np.ma.min(x), 0],
                [np.ma.max(y) * 1.5, np.ma.max(x), len(x) / 2],
            ),
        )
        popt = list(res.x) + [np.min(y)]
    return popt


def gaussfit3(x, y):
    """ A very simple (and relatively fast) gaussian fit
    gauss = A * exp(-(x-mu)**2/(2*sig**2)) + offset

    Parameters
    ----------
    x : array of shape (n,)
        x data
    y : array of shape (n,)
        y data

    Returns
    -------
    popt : list of shape (4,)
        Parameters A, mu, sigma**2, offset
    """
    gauss = gaussval2
    i = np.argmax(y[len(y) // 4 : len(y) * 3 // 4]) + len(y) // 4
    p0 = [y[i], x[i], 1, np.min(y)]

    with np.warnings.catch_warnings():
        np.warnings.simplefilter("ignore")
        popt, _ = curve_fit(gauss, x, y, p0=p0)

    return popt


def gaussfit4(x, y):
    """ A very simple (and relatively fast) gaussian fit
    gauss = A * exp(-(x-mu)**2/(2*sig**2)) + offset

    Assumes x is sorted

    Parameters
    ----------
    x : array of shape (n,)
        x data
    y : array of shape (n,)
        y data

    Returns
    -------
    popt : list of shape (4,)
        Parameters A, mu, sigma**2, offset
    """
    gauss = gaussval2
    i = len(x) // 2
    p0 = [y[i], x[i], 1, np.min(y)]

    with np.warnings.catch_warnings():
        np.warnings.simplefilter("ignore")
        popt, _ = curve_fit(gauss, x, y, p0=p0)

    return popt


def gaussfit_linear(x, y):
    """ Transform the gaussian fit into a linear least squares problem, and solve that instead of the non-linear curve fit
    For efficiency reasons. (roughly 10 times faster than the curve fit)

    Note, only works for positive values of y

    Parameters
    ----------
    x : array of shape (n,)
        x data
    y : array of shape (n,)
        y data

    Returns
    -------
    coef : tuple
        a, mu, sig, 0
    """
    x = x[y > 0]
    y = y[y > 0]

    offset = np.min(y)
    y = y - offset + 1e-12

    weights = y

    d = np.log(y)
    G = np.ones((x.size, 3), dtype=np.float)
    G[:, 0] = x ** 2
    G[:, 1] = x

    beta, _, _, _ = np.linalg.lstsq(
        (G.T * weights ** 2).T, d * weights ** 2, rcond=None
    )

    a = np.exp(beta[2] - beta[1] ** 2 / (4 * beta[0]))
    sig = -1 / (2 * beta[0])
    mu = -beta[1] / (2 * beta[0])

    return a, mu, sig, offset


def gaussval2(x, a, mu, sig, const):
    return a * np.exp(-(x - mu) ** 2 / (2 * sig)) + const


def gaussbroad(x, y, hwhm):
    """
    Apply gaussian broadening to x, y data with half width half maximum hwhm

    Parameters
    ----------
    x : array(float)
        x values
    y : array(float)
        y values
    hwhm : float > 0
        half width half maximum
    Returns
    -------
    array(float)
        broadened y values
    """

    # alternatively use:
    # from scipy.ndimage.filters import gaussian_filter1d as gaussbroad
    # but that doesn't have an x coordinate

    nw = len(x)
    dw = (x[-1] - x[0]) / (len(x) - 1)

    if hwhm > 5 * (x[-1] - x[0]):
        return np.full(len(x), sum(y) / len(x))

    nhalf = int(3.3972872 * hwhm / dw)
    ng = 2 * nhalf + 1  # points in gaussian (odd!)
    # wavelength scale of gaussian
    wg = dw * (np.arange(0, ng, 1, dtype=float) - (ng - 1) / 2)
    xg = (0.83255461 / hwhm) * wg  # convenient absisca
    gpro = (0.46974832 * dw / hwhm) * np.exp(-xg * xg)  # unit area gaussian w/ FWHM
    gpro = gpro / np.sum(gpro)

    # Pad spectrum ends to minimize impact of Fourier ringing.
    npad = nhalf + 2  # pad pixels on each end
    spad = np.concatenate((np.full(npad, y[0]), y, np.full(npad, y[-1])))

    # Convolve and trim.
    sout = np.convolve(spad, gpro)  # convolve with gaussian
    sout = sout[npad : npad + nw]  # trim to original data / length
    return sout  # return broadened spectrum.


def polyfit1d(x, y, degree=1, regularization=0):
    idx = np.arange(degree + 1)
    coeff = np.zeros(degree + 1)

    A = np.array([np.power(x, i) for i in idx], dtype=float).T
    b = y.ravel()

    L = np.array([regularization * i**2 for i in idx])
    I = np.linalg.inv(A.T @ A + np.diag(L))
    coeff = I @ A.T @ b

    coeff = coeff[::-1]

    return coeff


def polyfit2d(x, y, z, degree=1, max_degree=None, scale=True, plot=False):
    """A simple 2D plynomial fit to data x, y, z

    Parameters
    ----------
    x : array[n]
        x coordinates
    y : array[n]
        y coordinates
    z : array[n]
        data values
    degree : int, optional
        degree of the polynomial fit (default: 1)
    plot : bool, optional
        wether to plot the fitted surface and data (slow) (default: False)

    Returns
    -------
    coeff : array[degree+1, degree+1]
        the polynomial coefficients in numpy 2d format, i.e. coeff[i, j] for x**i * y**j
    """

    # Create combinations of degree of x and y
    # usually: [(0, 0), (1, 0), (0, 1), (1, 1), (2, 0), ....]
    if np.isscalar(degree):
        degree = int(degree)
        idx = [[i, j] for i, j in product(range(degree + 1), repeat=2)]
        coeff = np.zeros((degree + 1, degree + 1))
    else:
        degree = [int(degree[0]), int(degree[1])]
        idx = [[i, j] for i, j in product(range(degree[0] + 1), range(degree[1] + 1))]
        coeff = np.zeros((degree[0] + 1, degree[1] + 1))
        degree = max(degree)

    # We only want the combinations with maximum order COMBINED power
    idx = np.array(idx)
    if max_degree is not None:
        idx = idx[idx[:, 0] + idx[:, 1] <= max_degree]

    if scale:
        # Normalize x and y to avoid huge numbers
        norm_x = np.max(x).astype(float)
        norm_y = np.max(y).astype(float)
        x = x / norm_x
        y = y / norm_y
    else:
        norm_x = norm_y = 1

    # Calculate elements 1, x, y, x*y, x**2, y**2, ...
    A = np.array([np.power(x, i) * np.power(y, j) for i, j in idx], dtype=float).T
    b = z.ravel()

    if np.ma.is_masked(z):
        mask = z.mask
        b = z.compressed()
        A = A[~mask, :]

    # Do least squares fit
    C, *_ = lstsq(A, b)
    # C, *_ = np.linalg.lstsq(A, b, rcond=-1)

    # Reorder coefficients into numpy compatible 2d array
    for k, (i, j) in enumerate(idx):
        coeff[i, j] = C[k] / (norm_x ** i * norm_y ** j)

    if plot:
        # regular grid covering the domain of the data
        if x.size > 500:
            choice = np.random.choice(x.size, size=500, replace=False)
        else:
            choice = slice(None, None, None)
        x, y, z = x[choice], y[choice], z[choice]
        x, y = x * norm_x, y * norm_y
        X, Y = np.meshgrid(
            np.linspace(np.min(x), np.max(x), 20), np.linspace(np.min(y), np.max(y), 20)
        )
        Z = np.polynomial.polynomial.polyval2d(X, Y, coeff)
        fig = plt.figure()
        ax = fig.gca(projection="3d")
        ax.plot_surface(X, Y, Z, rstride=1, cstride=1, alpha=0.2)
        ax.scatter(x, y, z, c="r", s=50)
        plt.xlabel("X")
        plt.ylabel("Y")
        ax.set_zlabel("Z")
        ax.axis("equal")
        ax.axis("tight")
        plt.show()
    return coeff


def polyfit2d_2(x, y, z, degree=1, x0=None, loss="linear", method="lm", plot=False):

    if np.isscalar(degree):
        degree_x = degree_y = degree + 1
    else:
        degree_x = degree[0] + 1
        degree_y = degree[1] + 1

    polyval2d = np.polynomial.polynomial.polyval2d

    def func(c):
        c = c.reshape(degree_x, degree_y)
        value = polyval2d(x, y, c)
        return value - z

    if x0 is None:
        x0 = np.random.random_sample(degree_x * degree_y) * 0.1
    else:
        x0 = x0.ravel()

    res = least_squares(func, x0, loss=loss, method=method)
    coef = res.x
    coef.shape = degree_x, degree_y

    if plot:
        # regular grid covering the domain of the data
        if x.size > 500:
            choice = np.random.choice(x.size, size=500, replace=False)
        else:
            choice = slice(None, None, None)
        x, y, z = x[choice], y[choice], z[choice]
        X, Y = np.meshgrid(
            np.linspace(np.min(x), np.max(x), 20), np.linspace(np.min(y), np.max(y), 20)
        )
        Z = np.polynomial.polynomial.polyval2d(X, Y, coef)
        fig = plt.figure()
        ax = fig.gca(projection="3d")
        ax.plot_surface(X, Y, Z, rstride=1, cstride=1, alpha=0.2)
        ax.scatter(x, y, z, c="r", s=50)
        plt.xlabel("X")
        plt.ylabel("Y")
        ax.set_zlabel("Z")
        ax.axis("equal")
        ax.axis("tight")
        plt.show()
    return coef


def bezier_interp(x_old, y_old, x_new):
    """
    Bezier interpolation, based on the scipy methods

    This mostly sanitizes the input by removing masked values and duplicate entries
    Note that in case of duplicate entries (in x_old) the results are not well defined as only one of the entries is used and the other is discarded

    Parameters
    ----------
    x_old : array[n]
        old x values
    y_old : array[n]
        old y values
    x_new : array[m]
        new x values

    Returns
    -------
    y_new : array[m]
        new y values
    """

    # Handle masked arrays
    if np.ma.is_masked(x_old):
        x_old = np.ma.compressed(x_old)
        y_old = np.ma.compressed(y_old)

    # avoid duplicate entries in x
    assert x_old.size == y_old.size
    x_old, index = np.unique(x_old, return_index=True)
    y_old = y_old[index]

    knots, coef, order = scipy.interpolate.splrep(x_old, y_old)
    y_new = scipy.interpolate.BSpline(knots, coef, order)(x_new)
    return y_new


def safe_interpolation(x_old, y_old, x_new=None, fill_value=0):
    """
    'Safe' interpolation method that should avoid
    the common pitfalls of spline interpolation

    masked arrays are compressed, i.e. only non masked entries are used
    remove NaN input in x_old and y_old
    only unique x values are used, corresponding y values are 'random'
    if all else fails, revert to linear interpolation

    Parameters
    ----------
    x_old : array of size (n,)
        x values of the data
    y_old : array of size (n,)
        y values of the data
    x_new : array of size (m, ) or None, optional
        x values of the interpolated values
        if None will return the interpolator object
        (default: None)

    Returns
    -------
    y_new: array of size (m, ) or interpolator
        if x_new was given, return the interpolated values
        otherwise return the interpolator object
    """

    # Handle masked arrays
    if np.ma.is_masked(x_old):
        x_old = np.ma.compressed(x_old)
        y_old = np.ma.compressed(y_old)

    mask = np.isfinite(x_old) & np.isfinite(y_old)
    x_old = x_old[mask]
    y_old = y_old[mask]

    # avoid duplicate entries in x
    # also sorts data, which allows us to use assume_sorted below
    x_old, index = np.unique(x_old, return_index=True)
    y_old = y_old[index]

    try:
        interpolator = scipy.interpolate.interp1d(
            x_old,
            y_old,
            kind="cubic",
            fill_value=fill_value,
            bounds_error=False,
            assume_sorted=True,
        )
    except ValueError:
        logging.warning(
            "Could not instantiate cubic spline interpolation, using linear instead"
        )
        interpolator = scipy.interpolate.interp1d(
            x_old,
            y_old,
            kind="linear",
            fill_value=fill_value,
            bounds_error=False,
            assume_sorted=True,
        )

    if x_new is not None:
        return interpolator(x_new)
    else:
        return interpolator


def bottom(f, order=1, iterations=40, eps=0.001, poly=False, weight=1, **kwargs):
    """
    bottom tries to fit a smooth curve to the lower envelope
    of 1D data array f. Filter size "filter"
    together with the total number of iterations determine
    the smoothness and the quality of the fit. The total
    number of iterations can be controlled by limiting the
    maximum number of iterations (iter) and/or by setting
    the convergence criterion for the fit (eps)
    04-Nov-2000 N.Piskunov wrote.
    09-Nov-2011 NP added weights and 2nd derivative constraint as LAM2

    Parameters
    ----------
    f : Callable
        Function to fit
    filter : int
        Smoothing parameter of the optimal filter (or polynomial degree of poly is True)
    iter : int
        maximum number of iterations [def: 40]
    eps : float
        convergence level [def: 0.001]
    mn : float
        minimum function values to be considered [def: min(f)]
    mx : float
        maximum function values to be considered [def: max(f)]
    lam2 : float
        constraint on 2nd derivative
    weight : array(float)
        vector of weights.
    """

    mn = kwargs.get("min", np.min(f))
    mx = kwargs.get("max", np.max(f))
    lambda2 = kwargs.get("lambda2", -1)

    if poly:
        j = np.where((f >= mn) & (f <= mx))
        xx = np.linspace(-1, 1, num=len(f))
        fmin = np.min(f[j]) - 1
        fmax = np.max(f[j]) + 1
        ff = (f[j] - fmin) / (fmax - fmin)
        ff_old = np.copy(ff)
    else:
        fff = middle(
            f, order, iterations=iterations, eps=eps, weight=weight, lambda2=lambda2
        )
        fmin = min(f) - 1
        fmax = max(f) + 1
        fff = (fff - fmin) / (fmax - fmin)
        ff = (f - fmin) / (fmax - fmin) / fff
        ff_old = np.copy(ff)

    for _ in range(iterations):
        if poly:

            if order > 0:  # this is a bug in rsi poly routine
                t = median_filter(np.polyval(np.polyfit(xx, ff, order), xx), 3)
                t = np.clip(t - ff, 0, None) ** 2
                tmp = np.polyval(np.polyfit(xx, t, order), xx)
                dev = np.sqrt(np.nan_to_num(tmp))
            else:
                t = np.tile(np.polyfit(xx, ff, order), len(f))
                t = np.polyfit(xx, np.clip(t - ff, 0, None) ** 2, order)
                t = np.tile(t, len(f))
                dev = np.nan_to_num(t)
                dev = np.sqrt(t)
        else:
            t = median_filter(opt_filter(ff, order, weight=weight, lambda2=lambda2), 3)
            dev = np.sqrt(
                opt_filter(
                    np.clip(weight * (t - ff), 0, None),
                    order,
                    weight=weight,
                    lambda2=lambda2,
                )
            )
        ff = np.clip(
            np.clip(t - dev, ff, None), None, t
        )  # the order matters, t dominates
        dev2 = np.max(weight * np.abs(ff - ff_old))
        ff_old = ff
        if dev2 <= eps:
            break

    if poly:
        if order > 0:  # this is a bug in rsi poly routine
            t = median_filter(np.polyval(np.polyfit(xx, ff, order), xx), 3)
        else:
            t = np.tile(np.polyfit(xx, ff, order), len(f))
        return t * (fmax - fmin) + fmin
    else:
        return t * fff * (fmax - fmin) + fmin


def middle(
    f,
    param,
    x=None,
    iterations=40,
    eps=0.001,
    poly=False,
    weight=1,
    lambda2=-1,
    mn=None,
    mx=None,
):
    """
    middle tries to fit a smooth curve that is located
    along the "middle" of 1D data array f. Filter size "filter"
    together with the total number of iterations determine
    the smoothness and the quality of the fit. The total
    number of iterations can be controlled by limiting the
    maximum number of iterations (iter) and/or by setting
    the convergence criterion for the fit (eps)
    04-Nov-2000 N.Piskunov wrote.
    09-Nov-2011 NP added weights and 2nd derivative constraint as LAM2

    Parameters
    ----------
    f : Callable
        Function to fit
    filter : int
        Smoothing parameter of the optimal filter (or polynomial degree of poly is True)
    iter : int
        maximum number of iterations [def: 40]
    eps : float
        convergence level [def: 0.001]
    mn : float
        minimum function values to be considered [def: min(f)]
    mx : float
        maximum function values to be considered [def: max(f)]
    lam2 : float
        constraint on 2nd derivative
    weight : array(float)
        vector of weights.
    """
    mn = mn if mn is not None else np.min(f)
    mx = mx if mx is not None else np.max(f)

    f = np.asarray(f)

    if x is None:
        xx = np.linspace(-1, 1, num=f.size)
    else:
        xx = np.asarray(x)

    if poly:
        j = (f >= mn) & (f <= mx)
        n = np.count_nonzero(j)
        if n <= round(param):
            return f

        fmin = np.min(f[j]) - 1
        fmax = np.max(f[j]) + 1
        ff = (f[j] - fmin) / (fmax - fmin)
        ff_old = ff
    else:
        fmin = np.min(f) - 1
        fmax = np.max(f) + 1
        ff = (f - fmin) / (fmax - fmin)
        ff_old = ff
        n = len(f)

    for _ in range(iterations):
        if poly:
            param = round(param)
            if param > 0:
                t = median_filter(np.polyval(np.polyfit(xx, ff, param), xx), 3)
                tmp = np.polyval(np.polyfit(xx, (t - ff) ** 2, param), xx)
            else:
                t = np.tile(np.polyfit(xx, ff, param), len(f))
                tmp = np.tile(np.polyfit(xx, (t - ff) ** 2, param), len(f))
        else:
            t = median_filter(opt_filter(ff, param, weight=weight, lambda2=lambda2), 3)
            tmp = opt_filter(
                weight * (t - ff) ** 2, param, weight=weight, lambda2=lambda2
            )

        dev = np.sqrt(np.clip(tmp, 0, None))
        ff = np.clip(t - dev, ff, t + dev)
        dev2 = np.max(weight * np.abs(ff - ff_old))
        ff_old = ff

        # print(dev2)
        if dev2 <= eps:
            break

    if poly:
        xx = np.linspace(-1, 1, len(f))
        if param > 0:
            t = median_filter(np.polyval(np.polyfit(xx, ff, param), xx), 3)
        else:
            t = np.tile(np.polyfit(xx, ff, param), len(f))

    return t * (fmax - fmin) + fmin


def top(
    f,
    order=1,
    iterations=40,
    eps=0.001,
    poly=False,
    weight=1,
    lambda2=-1,
    mn=None,
    mx=None,
):
    """
    top tries to fit a smooth curve to the upper envelope
    of 1D data array f. Filter size "filter"
    together with the total number of iterations determine
    the smoothness and the quality of the fit. The total
    number of iterations can be controlled by limiting the
    maximum number of iterations (iter) and/or by setting
    the convergence criterion for the fit (eps)
    04-Nov-2000 N.Piskunov wrote.
    09-Nov-2011 NP added weights and 2nd derivative constraint as LAM2

    Parameters
    ----------
    f : Callable
        Function to fit
    filter : int
        Smoothing parameter of the optimal filter (or polynomial degree of poly is True)
    iter : int
        maximum number of iterations [def: 40]
    eps : float
        convergence level [def: 0.001]
    mn : float
        minimum function values to be considered [def: min(f)]
    mx : float
        maximum function values to be considered [def: max(f)]
    lam2 : float
        constraint on 2nd derivative
    weight : array(float)
        vector of weights.
    """
    mn = mn if mn is not None else np.min(f)
    mx = mx if mx is not None else np.max(f)

    f = np.asarray(f)
    xx = np.linspace(-1, 1, num=f.size)

    if poly:
        j = (f >= mn) & (f <= mx)
        if np.count_nonzero(j) <= round(order):
            raise ValueError("Not enough points")
        fmin = np.min(f[j]) - 1
        fmax = np.max(f[j]) + 1
        ff = (f - fmin) / (fmax - fmin)
        ff_old = ff
    else:
        fff = middle(
            f, order, iterations=iterations, eps=eps, weight=weight, lambda2=lambda2
        )
        fmin = np.min(f) - 1
        fmax = np.max(f) + 1
        fff = (fff - fmin) / (fmax - fmin)
        ff = (f - fmin) / (fmax - fmin) / fff
        ff_old = ff

    for _ in range(iterations):
        order = round(order)
        if poly:
            t = median_filter(np.polyval(np.polyfit(xx, ff, order), xx), 3)
            tmp = np.polyval(np.polyfit(xx, np.clip(ff - t, 0, None) ** 2, order), xx)
            dev = np.sqrt(np.clip(tmp, 0, None))
        else:
            t = median_filter(opt_filter(ff, order, weight=weight, lambda2=lambda2), 3)
            tmp = opt_filter(
                np.clip(weight * (ff - t), 0, None),
                order,
                weight=weight,
                lambda2=lambda2,
            )
            dev = np.sqrt(np.clip(tmp, 0, None))

        ff = np.clip(t - eps, ff, t + dev * 3)
        dev2 = np.max(weight * np.abs(ff - ff_old))
        ff_old = ff
        if dev2 <= eps:
            break

    if poly:
        t = median_filter(np.polyval(np.polyfit(xx, ff, order), xx), 3)
        return t * (fmax - fmin) + fmin
    else:
        return t * fff * (fmax - fmin) + fmin


def opt_filter(y, par, par1=None, weight=None, lambda2=-1, maxiter=100):
    """
    Optimal filtering of 1D and 2D arrays.
    Uses tridiag in 1D case and sprsin and linbcg in 2D case.
    Written by N.Piskunov 8-May-2000

    Parameters
    ----------
    f : array
        1d or 2d array
    xwidth : int
        filter width (for 2d array width in x direction (1st index)
    ywidth : int
        (for 2d array only) filter width in y direction (2nd index) if ywidth is missing for 2d array, it set equal to xwidth
    weight : array(float)
        an array of the same size(s) as f containing values between 0 and 1
    maxiter : int
        maximum number of iteration for filtering of 2d array
    """

    y = np.asarray(y)

    if y.ndim not in [1, 2]:
        raise ValueError("Input y must have 1 or 2 dimensions")

    if par < 1:
        par = 1

    # 1D case
    if y.ndim == 1 or (y.ndim == 2 and (y.shape[0] == 1 or y.shape[1] == 1)):
        if par < 0:
            return y
        y = y.ravel()
        n = y.size

        if weight is None:
            weight = np.ones(n)
        elif np.isscalar(weight):
            weight = np.full(n, weight)
        else:
            weight = weight[:n]

        if lambda2 > 0:
            # Apply regularization lambda
            aij = np.zeros((5, n))
            # 2nd lower subdiagonal
            aij[0, 2:] = lambda2
            # Lower subdiagonal
            aij[1, 1] = -par - 2 * lambda2
            aij[1, 2:-1] = -par - 4 * lambda2
            aij[1, -1] = -par - 2 * lambda2
            # Main diagonal
            aij[2, 0] = weight[0] + par + lambda2
            aij[2, 1] = weight[1] + 2 * par + 5 * lambda2
            aij[2, 2:-2] = weight[2:-2] + 2 * par + 6 * lambda2
            aij[2, -2] = weight[-2] + 2 * par + 5 * lambda2
            aij[2, -1] = weight[-1] + par + lambda2
            # Upper subdiagonal
            aij[3, 0] = -par - 2 * lambda2
            aij[3, 1:-2] = -par - 4 * lambda2
            aij[3, -2] = -par - 2 * lambda2
            # 2nd lower subdiagonal
            aij[4, 0:-2] = lambda2
            # RHS
            b = weight * y

            f = solve_banded((2, 2), aij, b)
        else:
            a = np.full(n, -abs(par))
            b = np.copy(weight) + abs(par)
            b[1:-1] += abs(par)
            aba = np.array([a, b, a])

            f = solve_banded((1, 1), aba, weight * y)

        return f
    else:
        # 2D case
        if par1 is None:
            par1 = par
        if par == 0 and par1 == 0:
            raise ValueError("par and par1 can't both be 0")
        n = y.size
        nc, nr = y.shape

        adiag = abs(par)
        bdiag = abs(par1)

        # Main diagonal first:
        # aa = np.zeros((nc, nr))
        # aa[0, 0] = 1. + adiag + bdiag
        # aa[1:-2, 0] = np.full(nc - 2, 1. + 2. * adiag + bdiag)
        # aa[-1, 0] = 1. + adiag + bdiag

        # aa = np.array(
        #     (
        #         np.full(nc - 2, 1. + 2. * adiag + bdiag),
        #         1. + adiag + bdiag,
        #         np.full(n - 2 * nc, 1. + 2. * adiag + 2. * bdiag),
        #         1. + adiag + bdiag,
        #         np.full(nc - 2, 1. + 2. * adiag + bdiag),
        #         1. + adiag + bdiag,
        #         np.full(n - 1, -adiag),
        #         np.full(n - 1, -adiag),
        #         np.full(n - nc, -bdiag),
        #         np.full(n - nc, -bdiag),
        #     )
        # )

        # col = np.arange(nr - 2) * nc + nc  # special cases:
        aaa = np.full(nr - 2, 1. + adiag + 2. * bdiag)
        # aa[col] = aaa  # last columns
        # aa[col + nc - 1] = aaa  # first column
        # col = n + np.arange(nr - 1) * nc + nc - 1
        # aa[col] = 0.
        # aa[col + n - 1] = 0.

        # col = np.array(
        #     (
        #         np.arange(n),
        #         np.arange(n - 1) + 1,
        #         np.arange(n - 1),
        #         np.arange(n - nc) + nc,
        #         np.arange(n - nc),
        #     )
        # )  # lower sub-diagonal for y

        # row = np.array(
        #     (
        #         np.arange(n),
        #         np.arange(n - 1),
        #         np.arange(n - 1) + 1,
        #         np.arange(n - nc),
        #         np.arange(n - nc) + nc,
        #     )
        # )  # lower sub-diagonal for y

        # aaa = sprsin(col, row, aa, n, thresh=-2. * (adiag > bdiag))
        # col = bdiag
        # row = adiag
        # aa = np.reshape(y, n)  # start with an initial guess at the solution.

        aa = solve(aaa, y)  # solve the linear system ax=b.
        aa.shape = nc, nr  # restore the shape of the result.
        return aaa


def helcorr(obs_long, obs_lat, obs_alt, ra2000, dec2000, jd, system="barycentric"):
    """
    calculates heliocentric Julian date, barycentric and heliocentric radial
    velocity corrections, using astropy functions

    Parameters
    ---------
    obs_long : float
        Longitude of observatory (degrees, western direction is positive)
    obs_lat : float
        Latitude of observatory (degrees)
    obs_alt : float
        Altitude of observatory (meters)
    ra2000 : float
        Right ascension of object for epoch 2000.0 (hours)
    dec2000 : float
        Declination of object for epoch 2000.0 (degrees)
    jd : float
        Julian date for the middle of exposure
    system : {"barycentric", "heliocentric"}, optional
        reference system of the result, barycentric: around earth-sun gravity center,
        heliocentric: around sun, usually barycentric is preferred (default: "barycentric)

    Returns
    -------
    correction : float
        radial velocity correction due to barycentre offset
    hjd : float
        Heliocentric Julian date for middle of exposure
    """

    jd = 2400000. + jd
    jd = time.Time(jd, format="jd")

    ra = coord.Longitude(ra2000, unit=u.hour)
    dec = coord.Latitude(dec2000, unit=u.degree)

    observatory = coord.EarthLocation.from_geodetic(obs_long, obs_lat, height=obs_alt)
    sky_location = coord.SkyCoord(ra, dec, obstime=jd, location=observatory)
    times = time.Time(jd, location=observatory)

    if system == "barycentric":
        correction = sky_location.radial_velocity_correction().to(u.km / u.s).value
        ltt = times.light_travel_time(sky_location)
    elif system == "heliocentric":
        correction = (
            sky_location.radial_velocity_correction("heliocentric").to(u.km / u.s).value
        )
        ltt = times.light_travel_time(sky_location, "heliocentric")
    else:
        raise AttributeError(
            "Could not parse system, values are: ('barycentric', 'heliocentric')"
        )

    times = (times.utc + ltt).value - 2400000

    return -correction, times
