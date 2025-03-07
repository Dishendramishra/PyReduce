"""
REDUCE script for spectrograph data

Authors
-------
Ansgar Wehrhahn  (ansgar.wehrhahn@physics.uu.se)
Thomas Marquart  (thomas.marquart@physics.uu.se)
Alexis Lavail    (alexis.lavail@physics.uu.se)
Nikolai Piskunov (nikolai.piskunov@physics.uu.se)

Version
-------
1.0 - Initial PyReduce

License
--------
...

"""

import logging
import os.path
import sys
import time
from os.path import join

import joblib
import matplotlib.pyplot as plt
import numpy as np
from astropy.io import fits

# PyReduce subpackages
from . import echelle, instruments, util
from .combine_frames import combine_bias, combine_flat
from .configuration import load_config
from .continuum_normalization import continuum_normalize, splice_orders
from .extract import extract
from .extraction_width import estimate_extraction_width
from .make_shear import Curvature as CurvatureModule
from .normalize_flat import normalize_flat
from .trace_orders import mark_orders
from .wavelength_calibration import \
    WavelengthCalibration as WavelengthCalibrationModule

# TODO Naming of functions and modules
# TODO License

# TODO automatic determination of the extraction width


def main(
    instrument="UVES",
    target="HD132205",
    night="????-??-??",
    modes="middle",
    steps=("bias", "flat", "orders", "norm_flat", "wavecal", "science", "continuum"),
    base_dir=None,
    input_dir=None,
    output_dir=None,
    configuration=None,
    order_range=None,
):
    """
    Main entry point for REDUCE scripts,
    default values can be changed as required if reduce is used as a script
    Finds input directories, and loops over observation nights and instrument modes

    Parameters
    ----------
    instrument : str, list[str]
        instrument used for the observation (e.g. UVES, HARPS)
    target : str, list[str]
        the observed star, as named in the folder structure/fits headers
    night : str, list[str]
        the observation nights to reduce, as named in the folder structure. Accepts bash wildcards (i.e. \*, ?), but then relies on the folder structure for restricting the nights
    modes : str, list[str], dict[{instrument}:list], None, optional
        the instrument modes to use, if None will use all known modes for the current instrument. See instruments for possible options
    steps : tuple(str), "all", optional
        which steps of the reduction process to perform
        the possible steps are: "bias", "flat", "orders", "norm_flat", "wavecal", "science"
        alternatively set steps to "all", which is equivalent to setting all steps
        Note that the later steps require the previous intermediary products to exist and raise an exception otherwise
    base_dir : str, optional
        base data directory that Reduce should work in, is prefixxed on input_dir and output_dir (default: use settings_pyreduce.json)
    input_dir : str, optional
        input directory containing raw files. Can contain placeholders {instrument}, {target}, {night}, {mode} as well as wildcards. If relative will use base_dir as root (default: use settings_pyreduce.json)
    output_dir : str, optional
        output directory for intermediary and final results. Can contain placeholders {instrument}, {target}, {night}, {mode}, but no wildcards. If relative will use base_dir as root (default: use settings_pyreduce.json)
    configuration : dict[str:obj], str, list[str], dict[{instrument}:dict,str], optional
        configuration file for the current run, contains parameters for different parts of reduce. Can be a path to a json file, or a dict with configurations for the different instruments. When a list, the order must be the same as instruments (default: settings_{instrument.upper()}.json)
    """
    if isinstance(instrument, str):
        instrument = [instrument]
    if isinstance(target, str):
        target = [target]
    if isinstance(night, str):
        night = [night]
    if isinstance(modes, str):
        modes = [modes]

    isNone = {
        "modes": modes is None,
        "base_dir": base_dir is None,
        "input_dir": input_dir is None,
        "output_dir": output_dir is None,
    }

    # Loop over everything
    for j, i in enumerate(instrument):
        # settings: default settings of PyReduce
        # config: paramters for the current reduction
        # info: constant, instrument specific parameters
        config = load_config(configuration, i, j)

        # load default settings from settings_pyreduce.json
        if isNone["base_dir"]:
            base_dir = config["reduce"]["base_dir"]
        if isNone["input_dir"]:
            input_dir = config["reduce"]["input_dir"]
        if isNone["output_dir"]:
            output_dir = config["reduce"]["output_dir"]

        input_dir = join(base_dir, input_dir)
        output_dir = join(base_dir, output_dir)

        info = instruments.instrument_info.get_instrument_info(i)

        if isNone["modes"]:
            mode = info["modes"]
        elif isinstance(modes, dict):
            mode = modes[i]
        else:
            mode = modes

        for t in target:
            log_file = join(base_dir.format(instrument=i, mode=mode, target=t), "logs/%s.log" % t)
            util.start_logging(log_file)

            for n in night:
                for m in mode:
                    # find input files and sort them by type
                    files, nights = instruments.instrument_info.sort_files(
                        input_dir, t, n, i, m, **config["instrument"]
                    )
                    if len(files) == 0:
                        logging.warning(
                            "No files found for instrument:%s, target:%s, night:%s, mode:%s",
                            i,
                            t,
                            n,
                            m,
                        )
                    for f, k in zip(files, nights):
                        logging.info("Instrument: %s", i)
                        logging.info("Target: %s", t)
                        logging.info("Observation Date: %s", k)
                        logging.info("Instrument Mode: %s", m)

                        if not isinstance(f, dict):
                            f = {1: f}
                        for key, _ in f.items():
                            logging.info("Group Identifier: %s", key)
                            logging.debug("Bias files:\n%s", f[key]["bias"])
                            logging.debug("Flat files:\n%s", f[key]["flat"])
                            logging.debug("Wavecal files:\n%s", f[key]["wavecal"])
                            logging.debug("Orderdef files:\n%s", f[key]["orders"])
                            logging.debug("Science files:\n%s", f[key]["science"])
                            reducer = Reducer(
                                f[key],
                                output_dir,
                                t,
                                i,
                                m,
                                k,
                                config,
                                order_range=order_range,
                            )
                            reducer.run_steps(steps=steps)


class Step:
    """ Parent class for all steps """

    def __init__(
        self,
        instrument,
        mode,
        extension,
        target,
        night,
        output_dir,
        order_range,
        **config,
    ):
        self._dependsOn = []
        self._loadDependsOn = []
        #:str: Name of the instrument
        self.instrument = instrument
        #:str: Name of the instrument mode
        self.mode = mode
        #:int: Number of the FITS extension to use
        self.extension = extension
        #:str: Name of the observation target
        self.target = target
        #:str: Date of the observation (as a string)
        self.night = night
        #:tuple(int, int): First and Last(+1) order to process
        self.order_range = order_range
        self.plot = config.get("plot", False)
        self._output_dir = output_dir

    def run(self, files, *args):
        """Execute the current step

        Parameters
        ----------
        files : list(str)
            data files required for this step

        Raises
        ------
        NotImplementedError
            needs to be implemented for each step
        """
        raise NotImplementedError

    def save(self, *args):
        """Save the results of this step

        Parameters
        ----------
        *args : obj
            things to save

        Raises
        ------
        NotImplementedError
            Needs to be implemented for each step
        """
        raise NotImplementedError

    def load(self):
        """Load results from a previous execution

        Raises
        ------
        NotImplementedError
            Needs to be implemented for each step
        """
        raise NotImplementedError

    @property
    def dependsOn(self):
        """list(str): Steps that are required before running this step"""
        return self._dependsOn

    @property
    def loadDependsOn(self):
        """list(str): Steps that are required before loading data from this step"""
        return self._loadDependsOn

    @property
    def output_dir(self):
        """str: output directory, may contain tags {instrument}, {night}, {target}, {mode}"""
        return self._output_dir.format(
            instrument=self.instrument,
            target=self.target,
            night=self.night,
            mode=self.mode,
        )

    @property
    def prefix(self):
        """str: temporary file prefix"""
        i = self.instrument.lower()
        m = self.mode.lower()
        return f"{i}_{m}"


class Mask(Step):
    """Load the bad pixel mask for the given instrument/mode"""

    def __init__(self, *args, **config):
        super().__init__(*args, **config)
        self.extension = 0
        self._mask_dir = config["directory"]

    @property
    def mask_dir(self):
        """str: Directory containing the mask data file"""
        this = os.path.dirname(__file__)
        return self._mask_dir.format(reduce=this)

    @property
    def mask_file(self):
        """str: Name of the mask data file"""
        i = self.instrument.lower()
        m = self.mode
        return f"mask_{i}_{m}.fits.gz"

    def run(self):
        """Load the mask file from disk

        Returns
        -------
        mask : array of shape (nrow, ncol)
            Bad pixel mask for this setting
        """
        return self.load()

    def load(self):
        """Load the mask file from disk

        Returns
        -------
        mask : array of shape (nrow, ncol)
            Bad pixel mask for this setting
        """
        mask_file = join(self.mask_dir, self.mask_file)
        try:
            mask, _ = util.load_fits(
            mask_file, self.instrument, self.mode, extension=self.extension
            )
            mask = ~mask.data.astype(bool)  # REDUCE mask are inverse to numpy masks
        except FileNotFoundError:
            logging.error("Bad Pixel Mask datafile %s not found. Using all pixels instead.", mask_file)
            mask = False
        return mask


class Bias(Step):
    """Calculates the master bias"""

    def __init__(self, *args, **config):
        super().__init__(*args, **config)
        self._dependsOn += ["mask"]
        self._loadDependsOn += ["mask"]

    @property
    def savefile(self):
        """str: Name of master bias fits file"""
        return join(self.output_dir, self.prefix + ".bias.fits")

    def save(self, bias, bhead):
        """Save the master bias to a FITS file

        Parameters
        ----------
        bias : array of shape (nrow, ncol)
            bias data
        bhead : FITS header
            bias header
        """
        bias = np.asarray(bias, dtype=np.float32)
        fits.writeto(
            self.savefile,
            data=bias,
            header=bhead,
            overwrite=True,
            output_verify="fix+warn",
        )

    def run(self, files, mask):
        """Calculate the master bias

        Parameters
        ----------
        files : list(str)
            bias files
        mask : array of shape (nrow, ncol)
            bad pixel map

        Returns
        -------
        bias : masked array of shape (nrow, ncol)
            master bias data, with the bad pixel mask applied
        bhead : FITS header
            header of the master bias
        """
        if len(files) == 0:
            logging.error("No bias files found. Using bias 0 instead.")
            return 0, []
        bias, bhead = combine_bias(
                files,
                self.instrument,
                self.mode,
                mask=mask,
                extension=self.extension,
                plot=self.plot,
        )
        self.save(bias.data, bhead)

        return bias, bhead

    def load(self, mask):
        """Load the master bias from a previous run

        Parameters
        ----------
        mask : array of shape (nrow, ncol)
            Bad pixel mask

        Returns
        -------
        bias : masked array of shape (nrow, ncol)
            master bias data, with the bad pixel mask applied
        bhead : FITS header
            header of the master bias
        """
        bias = fits.open(self.savefile)[0]
        bias, bhead = bias.data, bias.header
        bias = np.ma.masked_array(bias, mask=mask)
        return bias, bhead


class Flat(Step):
    """Calculates the master flat"""

    def __init__(self, *args, **config):
        super().__init__(*args, **config)
        self._dependsOn += ["mask", "bias"]
        self._loadDependsOn += ["mask"]

    @property
    def savefile(self):
        """str: Name of master bias fits file"""
        return join(self.output_dir, self.prefix + ".flat.fits")

    def save(self, flat, fhead):
        """Save the master flat to a FITS file

        Parameters
        ----------
        flat : array of shape (nrow, ncol)
            master flat data
        fhead : FITS header
            master flat header
        """
        flat = np.asarray(flat, dtype=np.float32)
        fits.writeto(
            self.savefile,
            data=flat,
            header=fhead,
            overwrite=True,
            output_verify="fix+warn",
        )

    def run(self, files, bias, mask):
        """Calculate the master flat, with the bias already subtracted

        Parameters
        ----------
        files : list(str)
            flat files
        bias : tuple(array of shape (nrow, ncol), FITS header)
            master bias and header
        mask : array of shape (nrow, ncol)
            Bad pixel mask

        Returns
        -------
        flat : masked array of shape (nrow, ncol)
            Master flat with bad pixel map applied
        fhead : FITS header
            Master flat FITS header
        """
        bias, bhead = bias
        flat, fhead = combine_flat(
            files,
            self.instrument,
            self.mode,
            mask=mask,
            extension=self.extension,
            bias=bias,
            plot=self.plot,
        )

        self.save(flat.data, fhead)
        return flat, fhead

    def load(self, mask):
        """Load master flat from disk

        Parameters
        ----------
        mask : array of shape (nrow, ncol)
            Bad pixel mask

        Returns
        -------
        flat : masked array of shape (nrow, ncol)
            Master flat with bad pixel map applied
        fhead : FITS header
            Master flat FITS header
        """
        flat = fits.open(self.savefile)[0]
        flat, fhead = flat.data, flat.header
        flat = np.ma.masked_array(flat, mask=mask)
        return flat, fhead


class OrderTracing(Step):
    """Determine the polynomial fits describing the pixel locations of each order"""

    def __init__(self, *args, **config):
        super().__init__(*args, **config)
        self._dependsOn += ["mask"]

        #:int: Minimum size of each cluster to be included in further processing
        self.min_cluster = config["min_cluster"]
        #:int: Size of the gaussian filter for smoothing
        self.filter_size = config["filter_size"]
        #:int: Background noise value threshold
        self.noise = config["noise"]
        #:int: Polynomial degree of the fit to each order
        self.fit_degree = config["degree"]

        self.degree_before_merge = config["degree_before_merge"]
        self.regularization = config["regularization"]
        self.closing_shape = config["closing_shape"]
        self.auto_merge_threshold = config["auto_merge_threshold"]
        self.merge_min_threshold = config["merge_min_threshold"]
        self.sigma = config["split_sigma"]
        #:int: Number of pixels at the edge of the detector to ignore
        self.border_width = config["border_width"]
        #:bool: Whether to use manual alignment
        self.manual = config["manual"]

    @property
    def savefile(self):
        """str: Name of the order tracing file"""
        return join(self.output_dir, self.prefix + ".ord_default.npz")

    def run(self, files, mask):
        """Determine polynomial coefficients describing order locations

        Parameters
        ----------
        files : list(str)
            Observation used for order tracing (should only have one element)
        mask : array of shape (nrow, ncol)
            Bad pixel mask

        Returns
        -------
        orders : array of shape (nord, ndegree+1)
            polynomial coefficients for each order
        column_range : array of shape (nord, 2)
            first and last(+1) column that carries signal in each order
        """
        order_img, _ = util.load_fits(
            files[0], self.instrument, self.mode, self.extension, mask=mask
        )

        orders, column_range = mark_orders(
            order_img,
            min_cluster=self.min_cluster,
            filter_size=self.filter_size,
            noise=self.noise,
            opower=self.fit_degree,
            degree_before_merge = self.degree_before_merge,
            regularization=self.regularization,
            closing_shape=self.closing_shape,
            border_width=self.border_width,
            manual=self.manual,
            auto_merge_threshold=self.auto_merge_threshold,
            merge_min_threshold=self.merge_min_threshold,
            sigma=self.sigma,
            plot=self.plot,
        )

        self.save(orders, column_range)

        return orders, column_range

    def save(self, orders, column_range):
        """Save order tracing results to disk

        Parameters
        ----------
        orders : array of shape (nord, ndegree+1)
            polynomial coefficients
        column_range : array of shape (nord, 2)
            first and last(+1) column that carry signal in each order
        """
        np.savez(
            self.savefile, orders=orders, column_range=column_range
        )

    def load(self):
        """Load order tracing results

        Returns
        -------
        orders : array of shape (nord, ndegree+1)
            polynomial coefficients for each order
        column_range : array of shape (nord, 2)
            first and last(+1) column that carries signal in each order
        """
        data = np.load(self.savefile, allow_pickle=True)
        orders = data["orders"]
        column_range = data["column_range"]
        return orders, column_range


class NormalizeFlatField(Step):
    """Calculate the 'normalized' flat field image"""
    def __init__(self, *args, **config):
        super().__init__(*args, **config)
        self._dependsOn += ["flat", "orders"]

        #:{'normalize'}: Extraction method to use
        self.extraction_method = config["extraction_method"]
        if self.extraction_method == "normalize":
            #:dict: arguments for the extraction
            self.extraction_kwargs = {
                "extraction_width": config["extraction_width"],
                "lambda_sf": config["smooth_slitfunction"],
                "lambda_sp": config["smooth_spectrum"],
                "osample": config["oversampling"],
                "swath_width": config["swath_width"],
            }
        else:
            raise ValueError(
                f"Extraction method {self.extraction_method} not supported for step 'norm_flat'"
            )
        #:tuple(int, int): Polynomial degrees for the background scatter fit, in row, column direction
        self.scatter_degree = config["scatter_degree"]
        #:int: Threshold of the normalized flat field (values below this are just 1)
        self.threshold = config["threshold"]
        self.sigma_cutoff = config["sigma_cutoff"]
        self.border_width = config["border_width"]

    @property
    def savefile(self):
        """str: Name of the blaze file"""
        return join(self.output_dir, self.prefix + ".flat_norm.npz")

    def run(self, flat, orders):
        """Calculate the 'normalized' flat field

        Parameters
        ----------
        flat : tuple(array, header)
            Master flat, and its FITS header
        orders : tuple(array, array)
            Polynomial coefficients for each order, and the first and last(+1) column containing signal

        Returns
        -------
        norm : array of shape (nrow, ncol)
            normalized flat field
        blaze : array of shape (nord, ncol)
            Continuum level as determined from the flat field for each order
        """
        flat, fhead = flat
        orders, column_range = orders

        norm, blaze = normalize_flat(
            flat,
            orders,
            gain=fhead["e_gain"],
            readnoise=fhead["e_readn"],
            dark=fhead["e_drk"],
            column_range=column_range,
            order_range=self.order_range,
            scatter_degree=self.scatter_degree,
            threshold=self.threshold,
            sigma_cutoff=self.sigma_cutoff,
            border_width=self.border_width,
            plot=self.plot,
            **self.extraction_kwargs,
        )

        blaze = np.ma.filled(blaze, 0)

        # Save data
        self.save(norm, blaze)

        return norm, blaze

    def save(self, norm, blaze):
        """Save normalized flat field results to disk

        Parameters
        ----------
        norm : array of shape (nrow, ncol)
            normalized flat field
        blaze : array of shape (nord, ncol)
            Continuum level as determined from the flat field for each order
        """
        np.savez(self.savefile, blaze=blaze, norm=norm)

    def load(self):
        """Load normalized flat field results from disk

        Returns
        -------
        norm : array of shape (nrow, ncol)
            normalized flat field
        blaze : array of shape (nord, ncol)
            Continuum level as determined from the flat field for each order
        """
        logging.info("Loading normalized flat field")
        data = np.load(self.savefile, allow_pickle=True)
        blaze = data["blaze"]
        norm = data["norm"]
        return norm, blaze


class WavelengthCalibration(Step):
    """Perform wavelength calibration"""
    def __init__(self, *args, **config):
        super().__init__(*args, **config)
        self._dependsOn += ["mask", "orders"]

        #:{'arc', 'optimal'}: Extraction method to use
        self.extraction_method = config["extraction_method"]
        if self.extraction_method == "arc":
            #:dict: arguments for the extraction
            self.extraction_kwargs = {"extraction_width": config["extraction_width"]}
        elif self.extraction_method == "optimal":
            self.extraction_kwargs = {
                "extraction_width": config["extraction_width"],
                "lambda_sf": config["smooth_slitfunction"],
                "lambda_sp": config["smooth_spectrum"],
                "osample": config["oversampling"],
                "swath_width": config["swath_width"],
            }
        else:
            raise ValueError(
                f"Extraction method {self.extraction_method} not supported for step 'wavecal'"
            )

        #:tuple(int, int): Polynomial degree of the wavelength calibration in order, column direction
        self.degree = config["degree"]
        #:bool: Whether to use manual alignment instead of cross correlation
        self.manual = config["manual"]
        #:float: residual threshold in m/s
        self.threshold = config["threshold"]
        #:int: Number of iterations in the remove lines, auto id cycle
        self.iterations = config["iterations"]
        #:{'1D', '2D'}: Whether to use 1d or 2d polynomials
        self.wavecal_mode = config["dimensionality"]
        self.nstep = config["nstep"]
        #:float: fraction of columns, to allow individual orders to shift
        self.shift_window = config["shift_window"]

    @property
    def savefile(self):
        """str: Name of the wavelength echelle file"""
        return join(self.output_dir, self.prefix + ".thar.npz")

    def run(self, files, orders, mask):
        """Perform wavelength calibration

        This consists of extracting the wavelength image
        and fitting a polynomial the the known spectral lines

        Parameters
        ----------
        files : list(str)
            wavelength calibration files
        orders : tuple(array, array)
            Polynomial coefficients of each order, and columns with signal of each order
        mask : array of shape (nrow, ncol)
            Bad pixel mask

        Returns
        -------
        wave : array of shape (nord, ncol)
            wavelength for each point in the spectrum
        thar : array of shape (nrow, ncol)
            extracted wavelength calibration image
        coef : array of shape (*ndegrees,)
            polynomial coefficients of the wavelength fit
        linelist : record array of shape (nlines,)
            Updated line information for all lines
        """
        orders, column_range = orders

        f = files[0]
        if len(files) > 1:
            # TODO: Give the user the option to select one?
            logging.warning(
                "More than one wavelength calibration file found. Will use: %s", f
            )

        # Load wavecal image
        orig, thead = util.load_fits(
            f, self.instrument, self.mode, self.extension, mask=mask
        )

        # Extract wavecal spectrum
        thar, _, _, _ = extract(
            orig,
            orders,
            gain=thead["e_gain"],
            readnoise=thead["e_readn"],
            dark=thead["e_drk"],
            column_range=column_range,
            extraction_type=self.extraction_method,
            order_range=self.order_range,
            plot=self.plot,
            **self.extraction_kwargs,
        )

        # load reference linelist
        reference = instruments.instrument_info.get_wavecal_filename(
            thead, self.instrument, self.mode
        )
        reference = np.load(reference, allow_pickle=True)
        linelist = reference["cs_lines"]

        module = WavelengthCalibrationModule(
            plot=self.plot,
            manual=self.manual,
            degree=self.degree,
            threshold=self.threshold,
            iterations=self.iterations,
            mode=self.wavecal_mode,
            nstep=self.nstep,
            shift_window=self.shift_window,
        )
        wave, coef = module.execute(thar, linelist)
        self.save(wave, thar, coef, linelist)
        return wave, thar, coef, linelist

    def save(self, wave, thar, coef, linelist):
        """Save the results of the wavelength calibration

        Parameters
        ----------
        wave : array of shape (nord, ncol)
            wavelength for each point in the spectrum
        thar : array of shape (nrow, ncol)
            extracted wavelength calibration image
        coef : array of shape (*ndegrees,)
            polynomial coefficients of the wavelength fit
        linelist : record array of shape (nlines,)
            Updated line information for all lines
        """
        np.savez(
            self.savefile,
            wave=wave,
            thar=thar,
            coef=coef,
            linelist=linelist,
        )

    def load(self):
        """Load the results of the wavelength calibration

        Returns
        -------
        wave : array of shape (nord, ncol)
            wavelength for each point in the spectrum
        thar : array of shape (nrow, ncol)
            extracted wavelength calibration image
        coef : array of shape (*ndegrees,)
            polynomial coefficients of the wavelength fit
        linelist : record array of shape (nlines,)
            Updated line information for all lines
        """
        data = np.load(self.savefile, allow_pickle=True)
        wave = data["wave"]
        thar = data["thar"]
        coef = data["coef"]
        linelist = data["linelist"]
        return wave, thar, coef, linelist

class LaserFrequencyComb(Step):
    """Improve the precision of the wavelength calibration with a laser frequency comb"""
    def __init__(self, *args, **config):
        super().__init__(*args, **config)
        self._dependsOn += ["wavecal", "orders", "mask"]
        self._loadDependsOn += ["wavecal"]

        #:{'arc', 'optimal'}: extraction method
        self.extraction_method = config["extraction_method"]
        if self.extraction_method == "arc":
            #:dict: keywords for the extraction
            self.extraction_kwargs = {"extraction_width": config["extraction_width"]}
        elif self.extraction_method == "optimal":
            self.extraction_kwargs = {
                "extraction_width": config["extraction_width"],
                "lambda_sf": config["smooth_slitfunction"],
                "lambda_sp": config["smooth_spectrum"],
                "osample": config["oversampling"],
                "swath_width": config["swath_width"],
            }
        else:
            raise ValueError(
                f"Extraction method {self.extraction_method} not supported for step 'freq_comb'"
            )

        #:tuple(int, int): polynomial degree of the wavelength fit
        self.degree = config["degree"]
        #:float: residual threshold in m/s above which to remove lines
        self.threshold = config["threshold"]
        #:{'1D', '2D'}: Whether to use 1D or 2D polynomials
        self.wavecal_mode = config["dimensionality"]
        self.nstep = config["nstep"]
        #:int: Width of the peaks for finding them in the spectrum
        self.peak_width = config["peak_width"]

    @property
    def savefile(self):
        """str: Name of the wavelength echelle file"""
        return join(self.output_dir, self.prefix + ".comb.npz")

    def run(self, files, wavecal, orders, mask):
        """Improve the wavelength calibration with a laser frequency comb (or similar)

        Parameters
        ----------
        files : list(str)
            observation files
        wavecal : tuple()
            results from the wavelength calibration step
        orders : tuple
            results from the order tracing step
        mask : array of shape (nrow, ncol)
            Bad pixel mask

        Returns
        -------
        wave : array of shape (nord, ncol)
            improved wavelength solution
        comb : array of shape (nord, ncol)
            extracted frequency comb image
        """
        wave, thar, coef, linelist = wavecal
        orders, column_range = orders

        f = files[0]
        orig, chead = util.load_fits(
            f, self.instrument, self.mode, self.extension, mask=mask
        )

        comb, _, _, _ = extract(
            orig,
            orders,
            gain=chead["e_gain"],
            readnoise=chead["e_readn"],
            dark=chead["e_drk"],
            extraction_type=self.extraction_method,
            column_range=column_range,
            order_range=self.order_range,
            plot=self.plot,
            **self.extraction_kwargs,
        )

        # for i in range(len(comb)):
        #     comb[i] -= comb[i][comb[i] > 0].min()
        #     comb[i] /= blaze[i] * comb[i].max() / blaze[i].max()

        module = WavelengthCalibrationModule(
            plot=self.plot,
            degree=self.degree,
            threshold=self.threshold,
            mode=self.wavecal_mode,
            nstep=self.nstep,
            lfc_peak_width=self.peak_width,
        )
        wave = module.frequency_comb(comb, wave, linelist)

        self.save(wave, comb)

        return wave, comb

    def save(self, wave, comb):
        """Save the results of the frequency comb improvement

        Parameters
        ----------
        wave : array of shape (nord, ncol)
            improved wavelength solution
        comb : array of shape (nord, ncol)
            extracted frequency comb image
        """
        np.savez(self.savefile, wave=wave, comb=comb)

    def load(self, wavecal):
        """Load the results of the frequency comb improvement if possible,
        otherwise just use the normal wavelength solution

        Parameters
        ----------
        wavecal : tuple
            results from the wavelength calibration step

        Returns
        -------
        wave : array of shape (nord, ncol)
            improved wavelength solution
        comb : array of shape (nord, ncol)
            extracted frequency comb image
        """
        try:
            data = np.load(self.savefile, allow_pickle=True)
        except FileNotFoundError:
            logging.warning(
                "No data for Laser Frequency Comb found, using regular wavelength calibration instead"
            )
            wave, thar, coef, linelist, orig = wavecal
            data = {"wave": wave, "comb": thar, "orig": orig}
        wave = data["wave"]
        comb = data["comb"]
        return wave, comb


class SlitCurvatureDetermination(Step):
    """Determine the curvature of the slit"""
    def __init__(self, *args, **config):
        super().__init__(*args, **config)
        self._dependsOn += ["orders", "mask"]

        #:{"arc"}: Extraction method to use
        self.extraction_method = "arc"
        #:tuple(int, 2): Number of pixels around each order to use in an extraction
        self.extraction_width = config["extraction_width"]
        #:int: Polynimal degree of the overall fit
        self.fit_degree = config["degree"]
        #:int: Number of iterations in the removal of bad lines loop
        self.max_iter = config["iterations"]
        #:float: how many sigma of bad lines to cut away
        self.sigma_cutoff = config["sigma_cutoff"]
        #:{'1D', '2D'}: Whether to use 1d or 2d polynomials
        self.curvature_mode = config["dimensionality"]
        self.verbose = config["verbose"]

    @property
    def savefile(self):
        """str: Name of the tilt/shear save file"""
        return join(self.output_dir, self.prefix + ".shear.npz")

    def run(self, files, orders, mask):
        """Determine the curvature of the slit

        Parameters
        ----------
        files : list(str)
            files to use for this
        orders : tuple
            results of the order tracing
        wavecal : tuple
            results from the wavelength calibration
        mask : array of shape (nrow, ncol)
            Bad pixel mask

        Returns
        -------
        tilt : array of shape (nord, ncol)
            first order slit curvature at each point
        shear : array of shape (nord, ncol)
            second order slit curvature at each point
        """
        orders, column_range = orders

        # TODO: Pick best image / combine images ?
        f = files[0]
        orig, head = util.load_fits(
            f, self.instrument, self.mode, self.extension, mask=mask
        )

        extracted, _, _, _ = extract(
            orig,
            orders,
            gain=head["e_gain"],
            readnoise=head["e_readn"],
            dark=head["e_drk"],
            extraction_type=self.extraction_method,
            column_range=column_range,
            order_range=self.order_range,
            plot=self.plot,
            extraction_width=self.extraction_width,
        )

        module = CurvatureModule(
            orders,
            column_range=column_range,
            extraction_width=self.extraction_width,
            order_range=self.order_range,
            fit_degree=self.fit_degree,
            max_iter=self.max_iter,
            sigma_cutoff=self.sigma_cutoff,
            mode=self.curvature_mode,
            plot=self.plot,
            verbose=self.verbose
        )
        tilt, shear = module.execute(extracted, orig)
        self.save(tilt, shear)

        return tilt, shear

    def save(self, tilt, shear):
        """Save results from the curvature

        Parameters
        ----------
        tilt : array of shape (nord, ncol)
            first order slit curvature at each point
        shear : array of shape (nord, ncol)
            second order slit curvature at each point
        """
        np.savez(self.savefile, tilt=tilt, shear=shear)

    def load(self):
        """Load the curvature if possible, otherwise return None, None, i.e. use vertical extraction

        Returns
        -------
        tilt : array of shape (nord, ncol)
            first order slit curvature at each point
        shear : array of shape (nord, ncol)
            second order slit curvature at each point
        """
        try:
            data = np.load(self.savefile, allow_pickle=True)
        except FileNotFoundError:
            logging.warning("No data for slit curvature found, setting it to 0.")
            data = {"tilt": None, "shear": None}

        tilt = data["tilt"]
        shear = data["shear"]
        return tilt, shear


class ScienceExtraction(Step):
    """Extract the science spectra"""
    def __init__(self, *args, **config):
        super().__init__(*args, **config)
        self._dependsOn += ["mask", "bias", "orders", "norm_flat", "curvature"]
        self._loadDependsOn += []

        #:{'arc', 'optimal'}: Extraction method
        self.extraction_method = config["extraction_method"]
        if self.extraction_method == "arc":
            #:dict: Keywords for the extraction algorithm
            self.extraction_kwargs = {"extraction_width": config["extraction_width"]}
        elif self.extraction_method == "optimal":
            self.extraction_kwargs = {
                "extraction_width": config["extraction_width"],
                "lambda_sf": config["smooth_slitfunction"],
                "lambda_sp": config["smooth_spectrum"],
                "osample": config["oversampling"],
                "swath_width": config["swath_width"],
            }
        else:
            raise ValueError(
                f"Extraction method {self.extraction_method} not supported for step 'science'"
            )

    def science_file(self, name):
        """Name of the science file in disk, based on the input file

        Parameters
        ----------
        name : str
            name of the observation file

        Returns
        -------
        name : str
            science file name
        """
        return util.swap_extension(name, ".science.ech", path=self.output_dir)

    def run(self, files, bias, orders, norm_flat, curvature, mask):
        """Extract Science spectra from observation

        Parameters
        ----------
        files : list(str)
            list of observations
        bias : tuple
            results from master bias step
        orders : tuple
            results from order tracing step
        norm_flat : tuple
            results from flat normalization
        curvature : tuple
            results from slit curvature step
        mask : array of shape (nrow, ncol)
            bad pixel map

        Returns
        -------
        heads : list(FITS header)
            FITS headers of each observation
        specs : list(array of shape (nord, ncol))
            extracted spectra
        sigmas : list(array of shape (nord, ncol))
            uncertainties of the extracted spectra
        columns : list(array of shape (nord, 2))
            column ranges for each spectra
        """
        bias, bhead = bias
        norm, blaze = norm_flat
        orders, column_range = orders
        tilt, shear = curvature

        heads, specs, sigmas, columns = [], [], [], []
        for fname in files:
            im, head = util.load_fits(
                fname,
                self.instrument,
                self.mode,
                self.extension,
                mask=mask,
                dtype=np.floating,
            )
            # Correct for bias and flat field
            im -= bias
            im /= norm

            # Optimally extract science spectrum
            spec, sigma, _, column_range = extract(
                im,
                orders,
                tilt=tilt,
                shear=shear,
                gain=head["e_gain"],
                readnoise=head["e_readn"],
                dark=head["e_drk"],
                extraction_type=self.extraction_method,
                column_range=column_range,
                order_range=self.order_range,
                plot=self.plot,
                **self.extraction_kwargs,
            )

            # save spectrum to disk
            self.save(fname, head, spec, sigma, column_range)
            heads.append(head)
            specs.append(spec)
            sigmas.append(sigma)
            columns.append(column_range)

        return heads, specs, sigmas, columns

    def save(self, fname, head, spec, sigma, column_range):
        """Save the results of one extraction

        Parameters
        ----------
        fname : str
            filename to save to
        head : FITS header
            FITS header
        spec : array of shape (nord, ncol)
            extracted spectrum
        sigma : array of shape (nord, ncol)
            uncertainties of the extracted spectrum
        column_range : array of shape (nord, 2)
            range of columns that have spectrum
        """
        nameout = self.science_file(fname)
        echelle.save(nameout, head, spec=spec, sig=sigma, columns=column_range)

    def load(self):
        """Load all science spectra from disk

        Returns
        -------
        heads : list(FITS header)
            FITS headers of each observation
        specs : list(array of shape (nord, ncol))
            extracted spectra
        sigmas : list(array of shape (nord, ncol))
            uncertainties of the extracted spectra
        columns : list(array of shape (nord, 2))
            column ranges for each spectra
        """
        files = [s for s in os.listdir(self.output_dir) if s.endswith(".science.ech")]

        heads, specs, sigmas, columns = [], [], [], []
        for fname in files:
            fname = join(self.output_dir, fname)
            science = echelle.read(
                fname,
                continuum_normalization=False,
                barycentric_correction=False,
                radial_velociy_correction=False,
            )
            heads.append(science.header)
            specs.append(science["spec"])
            sigmas.append(science["sig"])
            columns.append(science["columns"])

        return heads, specs, sigmas, columns


class ContinuumNormalization(Step):
    """Determine the continuum to each observation"""
    def __init__(self, *args, **config):
        super().__init__(*args, **config)
        self._dependsOn += ["science", "freq_comb", "norm_flat"]

    @property
    def savefile(self):
        """str: savefile name"""
        return join(self.output_dir, self.prefix + ".cont.npz")

    def run(self, science, freq_comb, norm_flat):
        """Determine the continuum to each observation
        Also splices the orders together

        Parameters
        ----------
        science : tuple
            results from science step
        freq_comb : tuple
            results from freq_comb step (or wavecal if those don't exist)
        norm_flat : tuple
            results from the normalized flatfield step

        Returns
        -------
        heads : list(FITS header)
            FITS headers of each observation
        specs : list(array of shape (nord, ncol))
            extracted spectra
        sigmas : list(array of shape (nord, ncol))
            uncertainties of the extracted spectra
        conts : list(array of shape (nord, ncol))
            continuum for each spectrum
        columns : list(array of shape (nord, 2))
            column ranges for each spectra
        """
        wave, comb = freq_comb
        heads, specs, sigmas, columns = science
        norm, blaze = norm_flat

        logging.info("Continuum normalization")
        conts = [None for _ in specs]
        for j, (spec, sigma) in enumerate(zip(specs, sigmas)):
            logging.info("Splicing orders")
            specs[j], wave, blaze, sigmas[j] = splice_orders(
                spec, wave, blaze, sigma, scaling=True, plot=self.plot
            )
            logging.info("Normalizing continuum")
            conts[j] = continuum_normalize(
                specs[j], wave, blaze, sigmas[j], plot=self.plot
            )

        self.save(heads, specs, sigmas, conts, columns)
        return heads, specs, sigmas, conts, columns

    def save(self, heads, specs, sigmas, conts, columns):
        """Save the results from the continuum normalization

        Parameters
        ----------
        heads : list(FITS header)
            FITS headers of each observation
        specs : list(array of shape (nord, ncol))
            extracted spectra
        sigmas : list(array of shape (nord, ncol))
            uncertainties of the extracted spectra
        conts : list(array of shape (nord, ncol))
            continuum for each spectrum
        columns : list(array of shape (nord, 2))
            column ranges for each spectra
        """
        value = {
            "heads": heads,
            "specs": specs,
            "sigmas": sigmas,
            "conts": conts,
            "columns": columns,
        }
        joblib.dump(value, self.savefile)

    def load(self):
        """Load the results from the continuum normalization

        Returns
        -------
        heads : list(FITS header)
            FITS headers of each observation
        specs : list(array of shape (nord, ncol))
            extracted spectra
        sigmas : list(array of shape (nord, ncol))
            uncertainties of the extracted spectra
        conts : list(array of shape (nord, ncol))
            continuum for each spectrum
        columns : list(array of shape (nord, 2))
            column ranges for each spectra
        """
        data = joblib.load(self.savefile)
        heads = data["heads"]
        specs = data["specs"]
        sigmas = data["sigmas"]
        conts = data["conts"]
        columns = data["columns"]
        return heads, specs, sigmas, conts, columns


class Finalize(Step):
    """Create the final output files"""
    def __init__(self, *args, **config):
        super().__init__(*args, **config)
        self._dependsOn += ["continuum", "freq_comb"]

    def output_file(self, number):
        """str: output file name"""
        out = f"{self.instrument.upper()}.{self.night}_{number}.ech"
        return os.path.join(self.output_dir, out)

    def run(self, continuum, freq_comb):
        """Create the final output files

        this is includes:
         - heliocentric corrections
         - creating one echelle file

        Parameters
        ----------
        continuum : tuple
            results from the continuum normalization
        freq_comb : tuple
            results from the frequency comb step (or wavelength calibration)
        """
        heads, specs, sigmas, conts, columns = continuum
        wave, comb = freq_comb

        # Combine science with wavecal and continuum
        for i, (head, spec, sigma, blaze) in enumerate(
            zip(heads, specs, sigmas, conts)
        ):
            head["e_erscle"] = ("absolute", "error scale")

            # Add heliocentric correction
            rv_corr, bjd = util.helcorr(
                head["e_obslon"],
                head["e_obslat"],
                head["e_obsalt"],
                head["ra"],
                head["dec"],
                head["e_jd"],
            )

            logging.debug("Heliocentric correction: %f km/s", rv_corr)
            logging.debug("Heliocentric Julian Date: %s", str(bjd))

            head["barycorr"] = rv_corr
            head["e_jd"] = bjd

            if self.plot:
                for j in range(spec.shape[0]):
                    plt.plot(wave[j], spec[j] / blaze[j])
                plt.show()

            fname = self.save(i, head, spec, sigma, blaze, wave, columns[i])
            logging.info("science file: %s", os.path.basename(fname))

    def save(self, i, head, spec, sigma, cont, wave, columns):
        """Save one output spectrum to disk

        Parameters
        ----------
        i : int
            individual number of each file
        head : FITS header
            FITS header
        spec : array of shape (nord, ncol)
            final spectrum
        sigma : array of shape (nord, ncol)
            final uncertainties
        cont : array of shape (nord, ncol)
            final continuum scales
        wave : array of shape (nord, ncol)
            wavelength solution
        columns : array of shape (nord, 2)
            columns that carry signal

        Returns
        -------
        out_file : str
            name of the output file
        """
        out_file = self.output_file(i)
        echelle.save(
            out_file, head, spec=spec, sig=sigma, cont=cont, wave=wave, columns=columns
        )
        return out_file


class Reducer:

    step_order = {
        "bias": 10,
        "flat": 20,
        "orders": 30,
        "norm_flat": 40,
        "wavecal": 50,
        "freq_comb": 60,
        "curvature": 70,
        "science": 80,
        "continuum": 90,
        "finalize": 100,
    }

    modules = {
        "mask": Mask,
        "bias": Bias,
        "flat": Flat,
        "orders": OrderTracing,
        "norm_flat": NormalizeFlatField,
        "wavecal": WavelengthCalibration,
        "freq_comb": LaserFrequencyComb,
        "curvature": SlitCurvatureDetermination,
        "science": ScienceExtraction,
        "continuum": ContinuumNormalization,
        "finalize": Finalize,
    }

    def __init__(
        self,
        files,
        output_dir,
        target,
        instrument,
        mode,
        night,
        config,
        order_range=None,
    ):
        """Reduce all observations from a single night and instrument mode

        Parameters
        ----------
        files: dict{str:str}
            Data files for each step
        output_dir : str
            directory to place output files in
        target : str
            observed targets as used in directory names/fits headers
        instrument : str
            instrument used for observations
        mode : str
            instrument mode used (e.g. "red" or "blue" for HARPS)
        night : str
            Observation night, in the same format as used in the directory structure/file sorting
        config : dict
            numeric reduction specific settings, like pixel threshold, which may change between runs
        info : dict
            fixed instrument specific values, usually header keywords for gain, readnoise, etc.
        """
        #:dict(str:str): Filenames sorted by usecase
        self.files = files
        self.output_dir = output_dir.format(
            instrument=instrument, target=target, night=night, mode=mode
        )

        info = instruments.instrument_info.get_instrument_info(instrument)
        extension = info["extension"]
        if isinstance(extension, list):
            imode = util.find_first_index(info["modes"], mode)
            extension = extension[imode]


        self.data = {}
        self.inputs = (
            instrument,
            mode,
            extension,
            target,
            night,
            output_dir,
            order_range,
        )
        self.config = config

    def run_module(self, step, load=False):
        # The Module this step is based on (An object of the Step class)
        module = self.modules[step](*self.inputs, **self.config.get(step, {}))

        # Load the dependencies necessary for loading/running this step
        dependencies = module.dependsOn if not load else module.loadDependsOn
        for dependency in dependencies:
            if dependency not in self.data.keys():
                self.data[dependency] = self.run_module(dependency, load=True)
        args = {d: self.data[d] for d in dependencies}

        # Try to load the data, if the step is not specifically given as necessary
        # If the intermediate data is not available, run it normally instead
        # But give a warning
        if load:
            try:
                logging.info("Loading data from step '%s'", step)
                data = module.load(**args)
            except FileNotFoundError:
                logging.warning(
                    "Intermediate File(s) for loading step %s not found. Running it instead.", step
                )
                data = self.run_module(step, load=False)
        else:
            logging.info("Running step '%s'", step)
            if step in self.files.keys():
                args["files"] = self.files[step]
            data = module.run(**args)

        self.data[step] = data
        return data

    def prepare_output_dir(self):
        # create output folder structure if necessary
        output_dir = self.output_dir
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

    def run_steps(self, steps="all"):
        """
        Execute the steps as required

        Parameters
        ----------
        steps : {tuple(str), "all"}, optional
            which steps of the reduction process to perform
            the possible steps are: "bias", "flat", "orders", "norm_flat", "wavecal", "freq_comb",
            "curvature", "science", "continuum", "finalize"
            alternatively set steps to "all", which is equivalent to setting all steps
        """
        self.prepare_output_dir()

        if steps == "all":
            steps = list(self.step_order.keys())

        steps = list(steps)
        steps.sort(key=lambda x: self.step_order[x])

        for step in steps:
            self.run_module(step)

        logging.debug("--------------------------------")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        # Command Line arguments passed
        args = util.parse_args()
    else:
        # Use "default" values set in main function
        args = {}

    start = time.time()
    main(**args)
    finish = time.time()
    print("Execution time: %f s" % (finish - start))
