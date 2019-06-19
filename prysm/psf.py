"""A base point spread function interface."""
from scipy import optimize, interpolate, special

from .conf import config
from .mathops import engine as e, jinc
from .coordinates import cart_to_polar
from .plotting import share_fig_ax
from .util import sort_xy
from .convolution import Convolvable
from .propagation import (
    prop_pupil_plane_to_psf_plane,
    prop_pupil_plane_to_psf_plane_units,
)

FIRST_AIRY_ZERO = 1.220
SECOND_AIRY_ZERO = 2.233
THIRD_AIRY_ZERO = 3.238
FIRST_AIRY_ENCIRCLED = 0.8377850436212378
SECOND_AIRY_ENCIRCLED = 0.9099305350850819
THIRD_AIRY_ENCIRCLED = 0.9376474743695488

AIRYDATA = {
    1: (FIRST_AIRY_ZERO, FIRST_AIRY_ENCIRCLED),
    2: (SECOND_AIRY_ZERO, SECOND_AIRY_ENCIRCLED),
    3: (THIRD_AIRY_ZERO, THIRD_AIRY_ENCIRCLED)
}


class PSF(Convolvable):
    """A Point Spread Function.

    Attributes
    ----------
    center_x : `int`
        center sample along x
    center_y : `int`
        center sample along y
    data : `numpy.ndarray`
        PSF normalized intensity data
    sample_spacing : `float`
        center to center spacing of samples
    x : `numpy.ndarray`
        x Cartesian axis locations of samples, 1D ndarray
    y `numpy.ndarray`
        y Cartesian axis locations of samples, 1D ndarray

    """
    def __init__(self, x, y, data):
        """Create a PSF object.

        Parameters
        ----------
        data : `numpy.ndarray`
            intensity data for the PSF
        x : `numpy.ndarray`
            1D ndarray defining x data grid
        y  : `numpy.ndarray`
            1D ndarray defining y data grid
        sample_spacing : `float`
            center-to-center spacing of samples, expressed in microns

        """
        super().__init__(x=x, y=y, data=data, has_analytic_ft=False)
        self._ee = {}
        self._mtf = None
        self._nu_p = None
        self._dnx = None
        self._dny = None

    def encircled_energy(self, radius):
        """Compute the encircled energy of the PSF.

        Parameters
        ----------
        radius : `float` or iterable
            radius or radii to evaluate encircled energy at

        Returns
        -------
        encircled energy
            if radius is a float, returns a float, else returns a list.

        Notes
        -----
        implementation of "Simplified Method for Calculating Encircled Energy,"
        Baliga, J. V. and Cohn, B. D., doi: 10.1117/12.944334

        """
        from .otf import MTF

        if hasattr(radius, '__iter__'):
            # user wants multiple points
            # um to mm, cy/mm assumed in Fourier plane
            radius_is_array = True
        else:
            radius_is_array = False

        # compute MTF from the PSF
        if self._mtf is None:
            self._mtf = MTF.from_psf(self)
            nx, ny = e.meshgrid(self._mtf.x, self._mtf.y)
            self._nu_p = e.sqrt(nx ** 2 + ny ** 2)
            # this is meaninglessly small and will avoid division by 0
            self._nu_p[self._nu_p == 0] = 1e-99
            self._dnx, self._dny = ny[1, 0] - ny[0, 0], nx[0, 1] - nx[0, 0]

        if radius_is_array:
            out = []
            for r in radius:
                if r not in self._ee:
                    self._ee[r] = _encircled_energy_core(self._mtf.data,
                                                         r / 1e3,
                                                         self._nu_p,
                                                         self._dnx,
                                                         self._dny)
                out.append(self._ee[r])
            return e.asarray(out)
        else:
            if radius not in self._ee:
                self._ee[radius] = _encircled_energy_core(self._mtf.data,
                                                          radius / 1e3,
                                                          self._nu_p,
                                                          self._dnx,
                                                          self._dny)
            return self._ee[radius]

    def ee_radius(self, energy=FIRST_AIRY_ENCIRCLED):
        """Radius associated with a certain amount of enclosed energy."""
        k, v = list(self._ee.keys()), list(self._ee.values())
        if energy in v:
            idx = v.index(energy)
            return k[idx]

        def optfcn(x):
            return (self.encircled_energy(x) - energy) ** 2

        # golden seems to perform best in presence of shallow local minima as in
        # the encircled energy
        return optimize.golden(optfcn)

    def ee_radius_diffraction(self, energy=FIRST_AIRY_ENCIRCLED):
        """Radius associated with a certain amount of enclosed energy for a diffraction limited circular pupil."""
        return _inverse_analytic_encircled_energy(self.fno, self.wavelength, energy)

    def ee_radius_ratio_to_diffraction(self, energy=FIRST_AIRY_ENCIRCLED):
        """Ratio of this PSF and the diffraction limited PSFs' radii enclosing a certain amount of energy."""
        self_rad = self.ee_radius(energy)
        diff_rad = _inverse_analytic_encircled_energy(self.fno, self.wavelength, energy)
        return self_rad / diff_rad

    # plotting -----------------------------------------------------------------

    def plot_encircled_energy(self, axlim=None, npts=50, lw=config.lw, zorder=config.zorder, fig=None, ax=None):
        """Make a 1D plot of the encircled energy at the given azimuth.

        Parameters
        ----------
        azimuth : `float`
            azimuth to plot at, in degrees
        axlim : `float`
            limits of axis, will plot [0, axlim]
        npts : `int`, optional
            number of points to use from [0, axlim]
        lw : `float`, optional
            line width
        zorder : `int` optional
            zorder
        fig : `matplotlib.figure.Figure`, optional
            Figure containing the plot
        ax : `matplotlib.axes.Axis`, optional:
            Axis containing the plot

        Returns
        -------
        fig : `matplotlib.figure.Figure`, optional
            Figure containing the plot
        ax : `matplotlib.axes.Axis`, optional:
            Axis containing the plot

        """
        if axlim is None:
            if len(self._ee) is not 0:
                xx, yy = sort_xy(self._ee.keys(), self._ee.values())
            else:
                raise ValueError('if no values for encircled energy have been computed, axlim must be provided')
        elif axlim is 0:
            raise ValueError('computing from 0 to 0 is stupid')
        else:
            xx = e.linspace(1e-5, axlim, npts)
            yy = self.encircled_energy(xx)

        fig, ax = share_fig_ax(fig, ax)
        ax.plot(xx, yy, lw=lw, zorder=zorder)
        ax.set(xlabel='Image Plane Distance [μm]',
               ylabel='Encircled Energy [Rel 1.0]',
               xlim=(0, axlim))
        return fig, ax

    # plotting -----------------------------------------------------------------

    # helpers ------------------------------------------------------------------

    def _renorm(self, to='peak'):
        """Renormalize the PSF to unit peak intensity.

        Parameters
        ----------
        to : `string`, {'peak', 'total'}
            renormalization target; produces a PSF of unit peak or total intensity

        Returns
        -------
        `PSF`
            a renormalized PSF instance

        """
        if to.lower() == 'peak':
            self.data /= self.data.max()
        elif to.lower() == 'total':
            ttl = self.data.sum()
            self.data /= ttl
        return self

    # helpers ------------------------------------------------------------------

    @staticmethod
    def from_pupil(pupil, efl, Q=config.Q, norm='max', radpower=1, incoherent=True):
        """Use scalar diffraction propogation to generate a PSF from a pupil.

        Parameters
        ----------
        pupil : `Pupil`
            Pupil, with OPD data and wavefunction
        efl : `int` or `float`
            effective focal length of the optical system, mm
        Q : `int` or `float`
            ratio of pupil sample count to PSF sample count; Q > 2 satisfies nyquist
        norm : `str`, {'max', 'radiometric'}, optional
            how to normalize the result, if radiometric will follow Born & Wolf with:
            I0 = P * A / (L^2 R^2) with
            P = radpower,
            A = integral over aperture,
            L = wavelength
            R = efl
        radpower : `float`
            total power of the incident beam over the clear aperture, W
            only used when norm='radiometric'
        incoherent: `bool`, optional
            if True, propagate the incoherent PSF, else propagate the coherent one

        Returns
        -------
        `PSF`
            A new PSF instance

        """
        # propagate PSF data
        fcn, ss, wvl = pupil.fcn, pupil.sample_spacing, pupil.wavelength
        data = prop_pupil_plane_to_psf_plane(fcn, Q)
        norm = norm.lower()
        if norm == 'max':
            coef = 1 / data.max()
        elif norm == 'radiometric':
            # C = P D / (L^2 R^2) from Principles of Optics.
            P = radpower
            S2 = (pupil._mask ** 2).sum()
            coef = 1 / S2 ** 2  # normalize by "S2" in GH_FFT language
            D = pupil._mask.sum() * (ss ** 2)
            coef_BornWolf = P * D / ((wvl * 1e-3) ** 2 * efl ** 2)  # wvl 1e-3 um => mm
            coef = coef * coef_BornWolf
        else:
            raise ValueError('unknown norm')

        data = data * coef
        ux, uy = prop_pupil_plane_to_psf_plane_units(fcn, ss, efl, wvl, Q)
        psf = PSF(x=ux, y=uy, data=data)

        psf.fno = efl / pupil.diameter
        psf.wavelength = wvl
        return psf

    @staticmethod
    def polychromatic(psfs, spectral_weights=None, interp_method='linear'):
        """Create a new PSF instance from an ensemble of monochromatic PSFs given spectral weights.

        The new PSF is the polychromatic PSF, assuming the wavelengths are
        sufficiently different that they do not interfere and the mode of
        imaging is incoherent.

        """
        if spectral_weights is None:
            spectral_weights = [1] * len(psfs)

        # find the most densely sampled PSF
        min_spacing = 1e99
        ref_idx = None
        ref_x = None
        ref_y = None
        ref_samples_x = None
        ref_samples_y = None
        for idx, psf in enumerate(psfs):
            if psf.sample_spacing < min_spacing:
                min_spacing = psf.sample_spacing
                ref_idx = idx
                ref_x = psf.x
                ref_y = psf.y
                ref_samples_x = psf.samples_x
                ref_samples_y = psf.samples_y

        merge_data = e.zeros((ref_samples_x, ref_samples_y, len(psfs)))
        for idx, psf in enumerate(psfs):
            # don't do anything to the reference PSF besides spectral scaling
            if idx is ref_idx:
                merge_data[:, :, idx] = psf.data * spectral_weights[idx]
            else:
                xv, yv = e.meshgrid(ref_x, ref_y)
                interpf = interpolate.RegularGridInterpolator((psf.y, psf.x), psf.data)
                merge_data[:, :, idx] = interpf((yv, xv), method=interp_method) * spectral_weights[idx]

        psf = PSF(data=merge_data.sum(axis=2), x=ref_x, y=ref_y)
        psf.spectral_weights = spectral_weights
        psf._renorm()
        return psf


class AiryDisk(Convolvable):
    """An airy disk, the PSF of a circular aperture."""
    def __init__(self, fno, wavelength, extent=None, samples=None):
        """Create a new AiryDisk.

        Parameters
        ----------
        fno : `float`
            F/# associated with the PSF
        wavelength : `float`
            wavelength of light, in microns
        extent : `float`
            cartesian window half-width, e.g. 10 will make an RoI 20x20 microns wide
        samples : `int`
            number of samples across full width

        """
        if samples is not None:
            x = e.linspace(-extent, extent, samples)
            y = e.linspace(-extent, extent, samples)
            xx, yy = e.meshgrid(x, y)
            rho, phi = cart_to_polar(xx, yy)
            data = airydisk(rho, fno, wavelength)
        else:
            x, y, data = None, None, None
        self.fno = fno
        self.wavelength = wavelength
        super().__init__(data=data, x=x, y=y)
        self.has_analytic_ft = True

    def analytic_ft(self, x, y):
        """Analytic fourier transform of an airy disk.

        Parameters
        ----------
        x : `numpy.ndarray`
            sample points in x axis
        y : `numpy.ndarray`
            sample points in y axis

        Returns
        -------
        `numpy.ndarray`
            2D numpy array containing the analytic fourier transform

        """
        from .otf import diffraction_limited_mtf
        r, p = cart_to_polar(x, y)
        return diffraction_limited_mtf(self.fno, self.wavelength, r*1e3)  # um to mm


def airydisk(unit_r, fno, wavelength):
    """Compute the airy disk function over a given spatial distance.

    Parameters
    ----------
    unit_r : `numpy.ndarray`
        ndarray with units of um
    fno : `float`
        F/# of the system
    wavelength : `float`
        wavelength of light, um

    Returns
    -------
    `numpy.ndarray`
        ndarray containing the airy pattern

    """
    u_eff = unit_r * e.pi / wavelength / fno
    return abs(2 * jinc(u_eff)) ** 2


def _encircled_energy_core(mtf_data, radius, nu_p, dx, dy):
    """Core computation of encircled energy, based on Baliga 1988.

    Parameters
    ----------
    mtf_data : `numpy.ndarray`
        unaliased MTF data
    radius : `float`
        radius of "detector"
    nu_p : `numpy.ndarray`
        radial spatial frequencies
    dx : `float`
        x frequency delta
    dy : `float`
        y frequency delta

    Returns
    -------
    `float`
        encircled energy for given radius

    """
    integration_fourier = special.j1(2 * e.pi * radius * nu_p) / nu_p
    dat = mtf_data * integration_fourier
    return radius * dat.sum() * dx * dy


def _analytical_encircled_energy(fno, wavelength, points):
    """Compute the analytical encircled energy for a diffraction limited circular aperture.

    Parameters
    ----------
    fno : `float`
        F/#
    wavelength : `float`
        wavelength of light
    points : `numpy.ndarray`
        radii of "detector"

    Returns
    -------
    `numpy.ndarray`
        encircled energy values

    """
    p = points * e.pi / fno / wavelength
    return 1 - special.j0(p)**2 - special.j1(p)**2


def _inverse_analytic_encircled_energy(fno, wavelength, energy=FIRST_AIRY_ENCIRCLED):
    def optfcn(x):
        return (_analytical_encircled_energy(fno, wavelength, x) - energy) ** 2

    return optimize.golden(optfcn)
