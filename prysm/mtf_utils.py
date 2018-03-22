"""Utilities for working with MTF data."""

import numpy as np
import pandas as pd

from scipy.interpolate import griddata

from .mathops import floor, ceil, sin, cos, radians
from .util import correct_gamma, share_fig_ax
from .io import read_trioptics_mtf_vs_field, read_trioptics_MTFvFvF


class MTFvFvF(object):
    """Abstract object representing a cube of MTF vs Field vs Focus data.

    Attributes
    ----------
    azimuth : `str`
        Azimuth associated with the data
    data : `numpy.ndarray`
        3D array of data in shape (focus, field, freq)
    field : `numpy.ndarray`
        array of fields associated with the field axis of data
    focus : `numpy.ndarray`
        array of focus associated with the focus axis of data
    freq : `numpy.ndarray`
        array of frequencies associated with the frequency axis of data

    """
    def __init__(self, data, focus, field, freq, azimuth):
        """Create a new MTFvFvF object.

        Parameters
        ----------
        data : `numpy.ndarray`
            3D array in the shape (focus,field,freq)
        focus : `iterable`
            1D set of the column units, in microns
        field : `iterable`
            1D set of the row units, in any units
        freq : `iterable`
            1D set of the z axis units, in cy/mm
        azimuth : `string` or `float`
            azimuth this data cube is associated with

        """
        self.data = data
        self.focus = focus
        self.field = field
        self.freq = freq
        self.azimuth = azimuth

    def plot2d(self, freq, symmetric=False, contours=True, interp_method='lanczos', fig=None, ax=None):
        """Create a 2D plot of the cube, an "MTF vs Field vs Focus" plot.

        Parameters
        ----------
        freq : `float`
            frequency to plot, will be rounded to the closest value present in the self.freq iterable
        symmetric : `bool`
            make the plot symmetric by mirroring it about the x-axis origin
        contours : `bool`
            plot contours, yes or no (T/F)
        interp_method : `string`
            interpolation method used for the plot
        fig : `matplotlib.figure.Figure`
            Figure to plot inside
        ax : `matplotlib.axes.Axis`
            Axis to plot inside

        Returns
        -------
        fig : `matplotlib.figure.Figure`
            figure containing the plot
        axis : `matplotlib.axes.Axis`
            axis containing the plot

        """
        ext_x = [self.field[0], self.field[-1]]
        ext_y = [self.focus[0], self.focus[-1]]
        freq_idx = np.searchsorted(self.freq, freq)

        # if the plot is symmetric, mirror the data
        if symmetric is True:
            dat = correct_gamma(
                np.concatenate((
                    self.data[:, ::-1, freq_idx],
                    self.data[:, :, freq_idx]),
                    axis=1))
            ext_x[0] = ext_x[1] * -1
        else:
            dat = correct_gamma(self.data[:, :, freq_idx])

        ext = [ext_x[0], ext_x[1], ext_y[0], ext_y[1]]

        fig, ax = share_fig_ax(fig, ax)
        im = ax.imshow(dat,
                       extent=ext,
                       origin='lower',
                       cmap='inferno',
                       clim=(0, 1),
                       interpolation=interp_method,
                       aspect='auto')

        if contours is True:
            contours = [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
            cs = ax.contour(dat, contours, colors='0.15', linewidths=0.75, extent=ext)
            ax.clabel(cs, fmt='%1.1f', rightside_up=True)

        fig.colorbar(im, label=f'MTF @ {freq} cy/mm', ax=ax, fraction=0.046)
        ax.set(xlim=(ext_x[0], ext_x[1]), xlabel='Image Height [mm]',
               ylim=(ext_y[0], ext_y[1]), ylabel=r'Focus [$\mu$m]')
        return fig, ax

    def plot_thrufocus_singlefield(self, field, freqs=(10, 20, 30, 40, 50), _range=100, fig=None, ax=None):
        """Create a plot of Thru-Focus MTF for a single field point.

        Parameters
        ----------
        field : `float`
            which field point to plot, in same units as self.field
        freqs : `iterable`
            frequencies to plot, will be rounded to the closest values present in the self.freq iterable
        _range : `float`
            +/- focus range to plot, symmetric
        fig : `matplotlib.figure.Figure`
            Figure to plot inside
        ax : `matplotlib.axes.Axis`
            Axis to plot inside

        Returns
        -------
        fig : `matplotlib.figure.Figure`
            figure containing the plot
        axis : `matplotlib.axes.Axis`
            axis containing the plot

        """
        field_idx = np.searchsorted(self.field, field)
        freq_idxs = [np.searchsorted(self.freq, f) for f in freqs]
        range_idxs = [np.searchsorted(self.focus, r) for r in (-_range, _range)]
        xaxis_pts = self.focus[range_idxs[0]:range_idxs[1]]

        mtf_arrays = []
        for idx, freq in zip(freq_idxs, freqs):
            data = self.data[range_idxs[0]:range_idxs[1], field_idx, idx]
            mtf_arrays.append(data)

        fig, ax = share_fig_ax(fig, ax)
        for data, freq in zip(mtf_arrays, freqs):
            ax.plot(xaxis_pts, data, label=freq)

        ax.legend(title=r'$\nu$ [cy/mm]')
        ax.set(xlim=(xaxis_pts[0], xaxis_pts[-1]), xlabel=r'Focus [$\mu m$]',
               ylim=(0, 1), ylabel='MTF [Rel. 1.0]')
        return fig, ax

    def trace_focus(self, algorithm='avg'):
        """Find the focus position in each field.

        This is, in effect, the "field curvature" for this azimuth.

        Parameters
        ----------
        algorithm : `str`
            algorithm to use to trace focus, currently only supports '0.5', see
            notes for a description of this technique

        Returns
        -------
        `numpy.ndarray`
            focal surface sag, in microns, vs field

        Notes
        -----
        Algorithm '0.5' uses the frequency that has its peak closest to 0.5
        on-axis to estimate the focus coresponding to the minimum RMS WFE
        condition.  This is based on the following assumptions:
            - Any combination of third, fifth, and seventh order spherical
                aberration will produce a focus shift that depends on
                frequency, and this dependence can be well fit by an
                equation of the form y(x) = ax^2 + bx + c.  If this is true,
                then the frequency which peaks at 0.5 will be near the
                vertex of the quadratic, which converges to the min RMS WFE
                condition.
            - Coma, while it enhances depth of field, does not shift the
                focus peak.
            - Astigmatism and field curvature are the dominant cause of any
                shift in best focus with field.
            - Chromatic aberrations do not influence the thru-focus MTF peak
                in a way that varies with field.

        Raises
        ------
        ValueError
            if an unsupported algorithm is entered

        """
        if algorithm == '0.5':
            # locate the frequency index on axis
            idx_axis = np.searchsorted(self.field, 0)
            idx_freq = abs(self.data[:, idx_axis, :].max(axis=0) - 0.5).argmin(axis=1)
            focus_idx = self.data[:, np.arange(self.data.shape[1]), idx_freq].argmax(axis=0)
            return self.focus[focus_idx], self.field
        elif algorithm.lower() in ('avg', 'average'):
            if self.freq[0] == 0:
                # if the zero frequency is included, exclude it from our calculations
                avg_idxs = self.data.argmax(axis=0)[:, 1:].mean(axis=1)
            else:
                avg_idxs = self.data.argmax(axis=0).mean(axis=1)

            # account for fractional indexes
            focus_out = avg_idxs.copy()
            for i, idx in enumerate(avg_idxs):
                li, ri = floor(idx), ceil(idx)
                lf, rf = self.focus[li], self.focus[ri]
                diff = rf - lf
                part = idx % 1
                focus_out[i] = lf + diff * part

            return focus_out, self.field
        else:
            raise ValueError('0.5 is only algorithm supported')

    @staticmethod
    def from_dataframe(df):
        """Return a pair of MTFvFvF objects for the tangential and one for the sagittal MTF.

        Parameters
        ----------
        df : `pandas.DataFrame`
            a dataframe

        Returns
        -------
        t_cube : `MTFvFvF`
            tangential MTFvFvF
        s_cube : `MTFvFvF`
            sagittal MTFvFvF

        """
        # copy the dataframe for manipulation
        df = df.copy()
        df.Fields = df.Field.round(4)
        df.Focus = df.Focus.round(6)
        sorted_df = df.sort_values(by=['Focus', 'Field', 'Freq'])
        T = sorted_df[sorted_df.Azimuth == 'Tan']
        S = sorted_df[sorted_df.Azimuth == 'Sag']
        focus = np.unique(df.Focus.as_matrix())
        fields = np.unique(df.Fields.as_matrix())
        freqs = np.unique(df.Freq.as_matrix())
        d1, d2, d3 = len(focus), len(fields), len(freqs)
        t_mat = T.as_matrix.reshape((d1, d2, d3))
        s_mat = S.as_matrix.reshape((d1, d2, d3))
        t_cube = MTFvFvF(data=t_mat, focus=focus, field=fields, freq=freqs, azimuth='Tan')
        s_cube = MTFvFvF(data=s_mat, focus=focus, field=fields, freq=freqs, azimuth='Sag')
        return t_cube, s_cube

    @staticmethod
    def from_trioptics_file(file_path):
        """Create a new MTFvFvF object from a trioptics file.

        Parameters
        ----------
        file_path : path_like
            path to a file

        Returns
        -------
        `MTFvFvF`
            new MTFvFvF object

        """
        return MTFvFvF(**read_trioptics_MTFvFvF(file_path))


def mtf_ts_extractor(mtf, freqs):
    """Extract the T and S MTF from a PSF object.

    Parameters
    ----------
    mtf : `MTF`
        MTF object
    freqs : iterable
        set of frequencies to extract

    Returns
    -------
    tan : `numpy.ndarray`
        array of tangential MTF values
    sag : `numpy.ndarray`
        array of sagittal MTF values

    """
    tan = mtf.exact_tan(freqs)
    sag = mtf.exact_sag(freqs)
    return tan, sag


def mtf_ts_to_dataframe(tan, sag, freqs, field=0, focus=0):
    """Create a Pandas dataframe from tangential and sagittal MTF data.

    Parameters
    ----------
    tan : `numpy.ndarray`
        vector of tangential MTF data
    sag : `numpy.ndarray`
        vector of sagittal MTF data
    freqs : iterable
        vector of spatial frequencies for the data
    field : `float`
        relative field associated with the data
    focus : `float`
        focus offset (um) associated with the data

    Returns
    -------
    pandas dataframe.

    """
    rows = []
    for f, s, t in zip(freqs, tan, sag):
        base_dict = {
            'Field': field,
            'Focus': focus,
            'Freq': f,
        }
        rows.append({**base_dict, **{
            'Azimuth': 'Tan',
            'MTF': t,
        }})
        rows.append({**base_dict, **{
            'Azimuth': 'Sag',
            'MTF': s,
        }})
    return pd.DataFrame(data=rows)


class MTFFFD(object):
    """An MTF Full-Field Display; stores MTF vs Field vs Frequency and supports plotting."""

    def __init__(self, data, field_x, field_y, freq):
        """Create a new MTFFFD object.

        Parameters
        ----------
        data : `numpy.ndarray`
            3D ndarray of data with axes field_x, field_y, freq
        field_x : `numpy.ndarray`
            1D array of x fields
        field_y : `numpy.ndarray`
            1D array of y fields
        freq : `numpy.ndarray`
            1D array of frequencies

        """
        self.data = data
        self.field_x = field_x
        self.field_y = field_y
        self.freq = freq

    def plot2d(self, freq, fig=None, ax=None):
        """Plot the MTF FFD.

        Parameters
        ----------
        freq : `float`
            frequency to plot at
        fig : `matplotlib.figure.Figure`
            figure containing the plot
        axis : `matplotlib.axes.Axis`
            axis containing the plot

        Returns
        -------
        fig : `matplotlib.figure.Figure`
            figure containing the plot
        axis : `matplotlib.axes.Axis`
            axis containing the plot

        """
        idx = np.searchsorted(self.freq, freq)
        extx = (self.field_x[0], self.field_x[-1])
        exty = (self.field_y[0], self.field_y[-1])
        fig, ax = share_fig_ax(fig, ax)
        im = ax.imshow(self.data[:, :, idx],
                       extent=[*extx, *exty],
                       origin='lower',
                       interpolation='gaussian',
                       cmap='inferno',
                       clim=(0, 1))
        ax.set(xlim=(-11, 11), xlabel='Image Plane X [mm]', ylim=(-8, 8), ylabel='Image Plane Y [mm]')
        fig.colorbar(im, label=f'MTF @ {freq} cy/mm', ax=ax, fraction=0.046)
        return fig, ax

    @staticmethod
    def from_dataframe(df, azimuth):
        """Create a new MTFFFD from a DataFrame.

        Parameters
        ----------
        df : `pandas.DataFrame`
            a pandas df
        azimuth : `str`
            which azimuth to extract

        Returns
        -------
        `MTFFFD`
            a new MTFFD object

        """
        raise NotImplemented('not yet complete, df schema needs to be designed')
        # return MTFFFD(data=dat, field_x=x, field_y=y, freq=freqs)

    @staticmethod
    def from_trioptics_files(paths, azimuths, upsample=10, ret=('tan', 'sag')):
        """Convert a set of trioptics files to MTF FFD object(s).

        Parameters
        ----------
        paths : path_like
            paths to trioptics files
        azimuths : iterable of `strs`
            azimuths, one per path
        ret : tuple, optional
            strings representing outputs, {'tan', 'sag'} are the only currently implemented options

        Returns
        -------
        `MTFFFD`
            MTF FFD object

        Raises
        ------
        NotImplemented
            return option is not available

        """
        # ret = (r.lower() for r in ret)
        # extract data from files
        azimuths = radians(np.asarray(azimuths, dtype=np.float64))
        freqs, xs, ys, ts, ss = [], [], [], [], []
        for path, angle in zip(paths, azimuths):
            d = read_trioptics_mtf_vs_field(path)
            imght, freq, t, s = d['field'], d['freq'], d['tan'], d['sag']
            x, y = imght * cos(angle), imght * sin(angle)
            freqs.append(freq)
            xs.append(x)
            ys.append(y)
            ts.append(t)
            ss.append(s)

        # convert to arrays and interpolate onto a regular 2D grid via a cubic interpolator
        xarr, yarr, farr = np.asarray(xs), np.asarray(ys), np.asarray(freqs)
        val_tan, val_sag = np.asarray(ts), np.asarray(ss)
        npts = len(xs[0]) * upsample
        xmin, xmax, ymin, ymax = xarr.min(), xarr.max(), yarr.min(), yarr.max()

        # loop through the frequencies and interpolate them all onto the regular output grid
        out_x, out_y = np.linspace(xmin, xmax, npts), np.linspace(ymin, ymax, npts)
        xx, yy = np.meshgrid(out_x, out_y)
        sample_pts = np.stack([xarr.ravel(), yarr.ravel()], axis=1)
        interpt, interps = [], []
        for idx in range(val_tan.shape[1]):
            datt = griddata(sample_pts, val_tan[:, idx, :].ravel(), (xx, yy), method='linear')
            dats = griddata(sample_pts, val_sag[:, idx, :].ravel(), (xx, yy), method='linear')
            interpt.append(datt)
            interps.append(dats)

        tan, sag = np.rollaxis(np.asarray(interpt), 0, 3), np.rollaxis(np.asarray(interps), 0, 3)
        if ret == ('tan', 'sag'):
            return MTFFFD(tan, out_x, out_y, farr[0, :]), MTFFFD(sag, out_x, out_y, farr[0, :])
        else:
            raise NotImplemented('other returns not implemented')


def plot_mtf_vs_field(data_dict, fig=None, ax=None):
    """Plot MTF vs Field.

    Parameters
    ----------
    data_dict : `dict`
        dictionary with keys tan, sag, fields, frequencies
    fig : `matplotlib.figure.Figure`
        figure containing the plot
    axis : `matplotlib.axes.Axis`
        axis containing the plot

    Returns
    -------
    fig : `matplotlib.figure.Figure`
        figure containing the plot
    axis : `matplotlib.axes.Axis`
        axis containing the plot

    """
    tan_mtf_array, sag_mtf_array = data_dict['tan'], data_dict['sag']
    fields, frequencies = data_dict['field'], data_dict['freq']
    freqs = _int_check_frequencies(frequencies)

    fig, ax = share_fig_ax(fig, ax)

    for idx in range(tan_mtf_array.shape[0]):
        l, = ax.plot(fields, tan_mtf_array[idx, :], label=freqs[idx])
        ax.plot(fields, sag_mtf_array[idx, :], c=l.get_color(), ls='--')

    ax.legend(title=r'$\nu$ [cy/mm]')
    ax.set(xlim=(0, 14), xlabel='Image Height [mm]',
           ylim=(0, 1), ylabel='MTF [Rel. 1.0]')
    return fig, ax


def _int_check_frequencies(frequencies):
    freqs = []
    for freq in frequencies:
        if freq % 1 == 0:
            freqs.append(int(freq))
        else:
            freqs.append(freq)
    return freqs
