"""
Find the continuum level

Currently only splices orders together
First guess of the continuum is provided by the flat field
"""

from itertools import chain

import matplotlib.pyplot as plt
import numpy as np

from . import util

# np.seterr("raise")


def splice_orders(spec, wave, cont, sigm, scaling=True, plot=False):
    """
    Splice orders together so that they form a continous spectrum
    This is achieved by linearly combining the overlaping regions

    Parameters
    ----------
    spec : array[nord, ncol]
        Spectrum to splice, with seperate orders
    wave : array[nord, ncol]
        Wavelength solution for each point
    cont : array[nord, ncol]
        Continuum, blaze function will do fine as well
    sigm : array[nord, ncol]
        Errors on the spectrum
    scaling : bool, optional
        If true, the spectrum/continuum will be scaled to 1 (default: False)
    plot : bool, optional
        If true, will plot the spliced spectrum (default: False)

    Raises
    ------
    NotImplementedError
        If neighbouring orders dont overlap

    Returns
    -------
    spec, wave, cont, sigm : array[nord, ncol]
        spliced spectrum
    """
    nord, _ = spec.shape  # Number of sp. orders, Order length in pixels

    # Just to be extra safe that they are all the same
    mask = np.ma.getmaskarray(spec) | (spec == 0) | (cont == 0)
    spec = np.ma.masked_array(spec, mask=mask)
    wave = np.ma.masked_array(np.ma.getdata(wave), mask=mask)
    cont = np.ma.masked_array(np.ma.getdata(cont), mask=mask)
    sigm = np.ma.masked_array(np.ma.getdata(sigm), mask=mask)

    if scaling:
        # Scale everything to roughly the same size, around spec/blaze = 1
        scale = np.ma.median(spec / cont, axis=1)
        cont *= scale[:, None]

    if plot:
        plt.subplot(411)
        plt.title("Before")
        for i in range(spec.shape[0]):
            plt.plot(wave[i], spec[i] / cont[i])
        plt.ylim([0, 2])

        plt.subplot(412)
        plt.title("Before Error")
        for i in range(spec.shape[0]):
            plt.plot(wave[i], sigm[i] / cont[i])
        plt.ylim((0, np.ma.median(sigm[i] / cont[i]) * 2))

    # Order with largest signal, everything is scaled relative to this order
    iord0 = np.argmax(np.ma.median(spec / cont, axis=1))

    # Loop from iord0 outwards, first to the top, then to the bottom
    tmp0 = chain(range(iord0, 0, -1), range(iord0, nord - 1))
    tmp1 = chain(range(iord0 - 1, -1, -1), range(iord0 + 1, nord))

    for iord0, iord1 in zip(tmp0, tmp1):
        # Get data for current order
        # Note that those are just references to parts of the original data
        # any changes will also affect spec, wave, cont, and sigm
        s0, s1 = spec[iord0], spec[iord1]
        w0, w1 = wave[iord0], wave[iord1]
        c0, c1 = cont[iord0], cont[iord1]
        u0, u1 = sigm[iord0], sigm[iord1]

        # Calculate Overlap
        i0 = np.ma.where((w0 >= np.ma.min(w1)) & (w0 <= np.ma.max(w1)))
        i1 = np.ma.where((w1 >= np.ma.min(w0)) & (w1 <= np.ma.max(w0)))

        # Orders overlap
        if i0[0].size > 0 and i1[0].size > 0:
            # Interpolate the overlapping region onto the wavelength grid of the other order
            tmpS0 = util.bezier_interp(w1, s1, w0[i0])
            tmpB0 = util.bezier_interp(w1, c1, w0[i0])
            tmpU0 = util.bezier_interp(w1, u1, w0[i0])

            tmpS1 = util.bezier_interp(w0, s0, w1[i1])
            tmpB1 = util.bezier_interp(w0, c0, w1[i1])
            tmpU1 = util.bezier_interp(w0, u0, w1[i1])

            # Combine the two orders weighted by the relative error
            wgt0 = np.ma.vstack([c0[i0].data / u0[i0].data, tmpB0 / tmpU0]) ** 2
            wgt1 = np.ma.vstack([c1[i1].data / u1[i1].data, tmpB1 / tmpU1]) ** 2

            s0[i0], utmp = np.ma.average(
                np.ma.vstack([s0[i0], tmpS0]), axis=0, weights=wgt0, returned=True
            )
            c0[i0] = np.ma.average([c0[i0], tmpB0], axis=0, weights=wgt0)
            u0[i0] = c0[i0] * utmp ** -0.5

            s1[i1], utmp = np.ma.average(
                np.ma.vstack([s1[i1], tmpS1]), axis=0, weights=wgt1, returned=True
            )
            c1[i1] = np.ma.average([c1[i1], tmpB1], axis=0, weights=wgt1)
            u1[i1] = c1[i1] * utmp ** -0.5
        else:  # Orders dont overlap
            raise NotImplementedError("Orders don't overlap, please test")
            c0 *= util.top(s0 / c0, 1, poly=True)
            scale0 = util.top(s0 / c0, 1, poly=True)
            scale0 = np.polyfit(w0, scale0, 1)

            scale1 = util.top(s1 / c1, 1, poly=True)
            scale1 = np.polyfit(w1, scale1, 1)

            xx = np.linspace(np.min(w0), np.max(w1), 100)

            # TODO test this
            # scale = np.sum(scale0[0] * scale1[0] * xx * xx + scale0[0] * scale1[1] * xx + scale1[0] * scale0[1] * xx + scale1[1] * scale0[1])
            scale = scale0[::-1, None] * scale1[None, ::-1]
            scale = np.sum(np.polynomial.polynomial.polyval2d(xx, xx, scale)) / np.sum(
                np.polyval(scale1, xx) ** 2
            )
            s1 *= scale

    if plot:
        plt.subplot(413)
        plt.title("After")
        for i in range(nord):
            plt.plot(wave[i], spec[i] / cont[i], label="order=%i" % i)
        plt.ylim((0, 2))

        plt.subplot(414)
        plt.title("Error")
        for i in range(nord):
            plt.plot(wave[i], sigm[i] / cont[i], label="order=%i" % i)
        plt.ylim((0, np.ma.median(sigm[i] / cont[i]) * 2))
        plt.show()

    return spec, wave, cont, sigm


class Plot_Normalization:
    def __init__(self, wsort, sB, new_wave, contB, iteration=0):
        plt.ion()
        self.fig = plt.figure()
        self.fig.suptitle(f"Iteration: {iteration}")

        self.ax = self.fig.add_subplot(111)
        self.line1 = self.ax.plot(wsort, sB, label="Spectrum")[0]
        self.line2 = self.ax.plot(new_wave, contB, label="Continuum Fit")[0]
        plt.legend()

        plt.show()

    def plot(self, wsort, sB, new_wave, contB, iteration):
        self.fig.suptitle(f"Iteration: {iteration}")
        self.line1.set_xdata(wsort)
        self.line1.set_ydata(sB)
        self.line2.set_xdata(new_wave)
        self.line2.set_ydata(contB)

        self.fig.canvas.draw()
        self.fig.canvas.flush_events()

    def close(self):
        plt.ioff()
        plt.close()


def continuum_normalize(
    spec,
    wave,
    cont,
    sigm,
    iterations=10,
    smooth_initial=1e5,
    smooth_final=5e6,
    scale_vert=1,
    plot=True,
):
    """ Fit a continuum to a spectrum by slowly approaching it from the top.
    We exploit here that the continuum varies only on large wavelength scales, while individual lines act on much smaller scales

    TODO automatically find good parameters for smooth_initial and smooth_final
    TODO give variables better names

    Parameters
    ----------
    spec : masked array of shape (nord, ncol)
        Observed input spectrum, masked values describe column ranges
    wave : masked array of shape (nord, ncol)
        Wavelength solution of the spectrum
    cont : masked array of shape (nord, ncol)
        Initial continuum guess, for example based on the blaze
    sigm : masked array of shape (nord, ncol)
        Uncertainties of the spectrum
    iterations : int, optional
        Number of iterations of the algorithm, 
        note that runtime roughly scales with the number of iterations squared 
        (default: 10)
    smooth_initial : float, optional
        Smoothing parameter in the initial runs, usually smaller than smooth_final (default: 1e5)
    smooth_final : float, optional
        Smoothing parameter of the final run (default: 5e6)
    scale_vert : float, optional
        Vertical scale of the spectrum. Usually 1 if a previous normalization exists (default: 1)
    plot : bool, optional
        Wether to plot the current status and results or not (default: True)

    Returns
    -------
    cont : masked array of shape (nord, ncol)
        New continuum
    """

    nord, ncol = spec.shape

    par2 = 1e-4
    par4 = 0.01 * (1 - np.clip(2, None, 1 / np.sqrt(np.ma.median(spec))))

    b = np.clip(cont, 1, None)
    for i in range(nord):
        b[i, ~b.mask[i]] = util.middle(b[i, ~b.mask[i]], 1)
    cont = b

    # Create new equispaced wavelength grid
    wmin = np.ma.min(wave)
    wmax = np.ma.max(wave)
    dwave = np.abs(wave[nord // 2, ncol // 2] - wave[nord // 2, ncol // 2 - 1]) * 0.5
    nwave = np.ceil((wmax - wmin) / dwave) + 1
    new_wave = np.linspace(wmin, wmax, nwave, endpoint=True)

    # Combine all orders into one big spectrum, sorted by wavelength
    wsort, j, index = np.unique(
        wave.compressed(), return_index=True, return_inverse=True
    )
    sB = (spec / cont).compressed()[j]

    # Get initial weights for each point
    weight = util.middle(sB, 0.5, x=wsort - wmin)
    weight = weight / util.middle(weight, 3 * smooth_initial) + np.concatenate(
        ([0], 2 * weight[1:-1] - weight[0:-2] - weight[2:], [0])
    )
    weight = np.clip(weight, 0, None)
    # TODO for some reason the interpolation messes up, use linear instead for now
    # weight = util.safe_interpolation(wsort, weight, new_wave)
    weight = np.interp(new_wave, wsort, weight)
    weight /= np.max(weight)

    # Interpolate Spectrum onto the new grid
    # ssB = util.safe_interpolation(wsort, sB, new_wave)
    ssB = np.interp(new_wave, wsort, sB)
    # Keep the scale of the continuum
    bbb = util.middle(cont.compressed()[j], 1)

    contB = 1
    for i in range(iterations):
        # Find new approximation of the top, smoothed by some parameter
        c = ssB / contB
        for _ in range(iterations):
            _c = util.top(
                c, smooth_initial, eps=par2, weight=weight, lambda2=smooth_final
            )
            c = np.clip(_c, c, None)
        c = (
            util.top(c, smooth_initial, eps=par4, weight=weight, lambda2=smooth_final)
            * contB
        )

        # Scale it and update the weights of each point
        contB = c * scale_vert
        contB = util.middle(contB, 1)
        weight = np.clip(ssB / contB, None, contB / np.clip(ssB, 1, None))

        # Plot the intermediate results
        if plot:
            if i == 0:
                p = Plot_Normalization(wsort, sB, new_wave, contB, i)
            else:
                p.plot(wsort, sB, new_wave, contB, i)

    # Need to close the plot afterwards
    if plot:
        p.close()

    # Calculate the new continuum from intermediate values
    # new_cont = util.safe_interpolation(new_wave, contB, wsort)
    new_cont = np.interp(wsort, new_wave, contB)
    cont[~cont.mask] = (new_cont * bbb)[index]

    # Final output plot
    if plot:
        plt.plot(wave.ravel(), spec.ravel(), label="spec")
        plt.plot(wave.ravel(), cont.ravel(), label="cont")
        plt.legend(loc="best")
        plt.xlabel("Wavelength [A]")
        plt.ylabel("Flux")
        plt.show()

    return cont
