"""
Utility functions and classes for specter

Stephen Bailey
Fall 2012
"""

from __future__ import absolute_import, division, print_function, unicode_literals

import os
import math
import numpy as np
import scipy.signal
from scipy.special import legendre
from scipy.sparse import spdiags
from scipy.signal import convolve, convolve2d
from specter.util import pixspline
from time import time

_t0 = 0.0
def _timeit():
    global _t0
    tx = time()
    dt = tx - _t0
    _t0 = tx
    return dt
    
#- 2D Linear interpolator
class LinearInterp2D(object):
    """
    Linear interpolation on a 2D grid.  Allows values to be interpolated
    to be multi-dimensional.
    """
    def __init__(self, x, y, data):
        """
        x : array of x coordinates
        y : array of y coordinates
        data[ix, iy, ...] : 3 or more dimensional array of data to interpolate
            first two coordinates are x and y
        """
        self.x = np.array(x)
        self.y = np.array(y)
        self.data = np.array(data)

    def __call__(self, x, y):
        """
        Evaluate data at (x,y)
        """
        #- TODO: compare speed to solution at
        #- http://stackoverflow.com/questions/12729228/simple-efficient-bilinear-interpolation-of-images-in-numpy-and-python
        
        #- Find where we are in grid
        #- clip to 1 because we will use i and i-1
        #- clip to len(x)-1 to allow extrapolation beyond grid boundary
        ix = np.searchsorted(self.x, x).clip(1, len(self.x)-1)
        iy = np.searchsorted(self.y, y).clip(1, len(self.y)-1)
        
        #- Interpolation distances from points
        dx = (x - self.x[ix-1]) / (self.x[ix] - self.x[ix-1])
        dy = (y - self.y[iy-1]) / (self.y[iy] - self.y[iy-1])

        #- Interpolate, allowing x and/or y to be multi-dimensional
        #- NOTE: these are the slow steps, about equal time each
        
        #- Original code with what appears to be vestigial transposes
        # data1 = (self.data[ix-1,iy-1].T*(1-dx) + self.data[ix,iy-1].T*dx).T
        # data2 = (self.data[ix-1,iy].T*(1-dx) + self.data[ix,iy].T*dx).T
        # dataxy = (data1.T*(1-dy) + data2.T*dy).T

        #- Updated without transposes
        data1 = (self.data[ix-1,iy-1]*(1-dx) + self.data[ix,iy-1]*dx)
        data2 = (self.data[ix-1,iy]*(1-dx) + self.data[ix,iy]*dx)
        dataxy = (data1*(1-dy) + data2*dy)

        return dataxy
        
def rebin_image(image, n):
    """
    rebin 2D array pix into bins of size n x n
    
    New binsize must be evenly divisible into original pix image
    """
    assert image.shape[0] % n == 0
    assert image.shape[1] % n == 0
    
    s = image.shape[0]//n, n, image.shape[1]//n, n
    return image.reshape(s).sum(-1).sum(1)

    
#- Utility functions for sinc shifting pixelated PSFs
def _sincfunc(x, dx, dampfac=3.25):
    """sinc helper function for sincshift()"""
    if dx != 0.0:
        xx = (x+dx)*np.pi  #- cache shifted array for 30% faster evals
        return np.exp( -(xx/(dampfac*np.pi))**2 ) * np.sin(xx) / xx
    else:
        xx = np.zeros(len(x))
        xx[len(x)//2] = 1.0
        return xx

#- Implementation note: the typical PSF image is 15x15.
#- fftconvolve is not faster than convolve for images this small
def sincshift(image, dx, dy, sincrad=10, dampfac=3.25):
    """
    Return image shifted by dx, dy using sinc interpolation.
    
    For speed, do each dimension independently which can introduce edge
    effects.  Also see sincshift2d().
    """
    s = np.arange(-sincrad, sincrad+1.0)
    imgshape = image.shape

    if abs(dx) > 1e-6:
        sincx = _sincfunc(s, -dx, dampfac=dampfac)
        image = convolve(image.ravel(), sincx, mode='same')
        image = image.reshape(imgshape)

    if abs(dy) > 1e-6:
        sincy = _sincfunc(s, -dy, dampfac=dampfac)
        image = convolve(image.T.ravel(), sincy, mode='same')
        image = image.reshape(imgshape[-1::-1]).T

    return image

def sincshift2d(image, dx, dy, sincrad=10, dampfac=3.25):
    """
    Return image shifted by dx, dy using full 2D sinc interpolation
    """
    s = np.arange(-sincrad, sincrad+1.0)
    sincx = _sincfunc(s, -dx, dampfac=dampfac)
    sincy = _sincfunc(s, -dy, dampfac=dampfac)
    kernel = np.outer(sincy, sincx)
    newimage = convolve2d(image, kernel, mode='same')
    return newimage

from scipy.special import erf
def gaussint(x, mean=0.0, sigma=1.0):
    """
    Return integral from -inf to x of normalized Gaussian with mean and sigma
    """
    z = (x - mean) / (math.sqrt(2) * sigma)
    return (erf(z) + 1.0) / 2.0
    
def gausspix(x, mean=0.0, sigma=1.0):
    """
    Return Gaussian(mean,sigma) integrated over unit width pixels centered at x[].
    """
    edges = np.concatenate((x-0.5, x[-1:]+0.5))
    integrals = gaussint(edges, mean=mean, sigma=sigma)
    return integrals[1:] - integrals[0:-1]
    
def weighted_solve(A, b, w):
    """
    Solve `A x = b` with weights `w` on `b`
    Returns x, inverseCovarance(x)
    """
    assert len(b) == len(w)
    
    n = len(b)
    W = spdiags(w, [0,], n, n)
    y = A.T.dot(W.dot(b))
    iCov = A.T.dot(W.dot(A))
    x = np.linalg.lstsq(iCov, y)[0]
    return x, iCov

def trapz(edges, xp, yp):
    """
    Perform trapezoidal integration between edges using sampled function
    yp vs. xp.  Returns array of length len(edges)-1.
        
    Input xp array must be sorted in ascending order.
    
    See also numpy.trapz, which integrates a single array
    """
    if np.any(np.diff(xp) < 0.0):
        raise ValueError("Input x must be sorted in increasing order")
    
    if len(xp) != len(yp):
        raise ValueError("xp and yp must have same length")

    yedge = np.interp(edges, xp, yp)
    result = np.zeros(len(edges)-1)
    iedge = np.searchsorted(xp, edges)
    for i in range(len(edges)-1):
        ilo, ihi = iedge[i], iedge[i+1]
        xx = np.concatenate( (edges[i:i+1], xp[ilo:ihi], edges[i+1:i+2]) )
        yy = np.concatenate( (yedge[i:i+1], yp[ilo:ihi], yedge[i+1:i+2]) )
        result[i] = np.trapz(yy, xx)
        
    return result
    
def resample(x, xp, yp, xedges=False, xpedges=False):
    """
    IN PROGRESS.  Resample a spectrum to a new binning using PixelSpline

    1 <= x.ndim <= xp.ndim <= yp.ndim <= 2
    """

    assert 1 <= x.ndim
    assert x.ndim <= xp.ndim
    assert xp.ndim <= yp.ndim
    assert yp.ndim <= 2

    input_edges = xp if xpedges else pixspline.cen2bound(xp)
    ys = pixspline.PixelSpline(input_edges, yp)
    
    edges = x if xedges else pixspline.cen2bound(x)
    return ys.resample(edges)

#- Faster versions than np.outer, which has to do type checking and raveling
try:
    #- ~3x faster if numba is installed
    if 'NUMBA_DISABLE_JIT' in os.environ:
        raise ImportError
    import numba
    @numba.jit
    def outer(x, y, out):
        for i in range(len(x)):
            for j in range(len(y)):
                out[i,j] = x[i] * y[j]
        return out

except ImportError:
    #- 1.5x faster otherwise
    def outer(x, y, out):
        return np.multiply(x[:, None], y[None, :], out)
    

#have faster numba version of legval if numba is available
try:
    #this is the faster, flexible, most readable version
    if 'NUMBA_DISABLE_JIT' in os.environ:
        raise ImportError
    import numba
    @numba.jit(nopython=True,cache=True)
    def legval_numba(x, c):
        nd=len(c)
        ndd=nd
        xlen = x.size
        c0=c[-2]*np.ones(xlen)
        c1=c[-1]*np.ones(xlen)
        for i in range(3, ndd + 1):
            tmp = c0
            nd = nd - 1
            nd_inv = 1/nd
            c0 = c[-i] - (c1*(nd - 1))*nd_inv
            c1 = tmp + (c1*x*(2*nd - 1))*nd_inv
        return c0 + c1*x

except ImportError:
    #in the event there is no numba, revert to the orig legval
    #this is an exact copy of numpy's legval
    def legval_numba(x, c, tensor=True):
        c = np.array(c, ndmin=1, copy=0)
        if c.dtype.char in '?bBhHiIlLqQpP':
            c = c.astype(np.double)
        if isinstance(x, (tuple, list)):
            x = np.asarray(x)
        if isinstance(x, np.ndarray) and tensor:
            c = c.reshape(c.shape + (1,)*x.ndim)

        if len(c) == 1:
            c0 = c[0]
            c1 = 0
        elif len(c) == 2:
            c0 = c[0]
            c1 = c[1]
        else:
            nd = len(c)
            c0 = c[-2]
            c1 = c[-1]
            for i in range(3, len(c) + 1):
                tmp = c0
                nd = nd - 1
                c0 = c[-i] - (c1*(nd - 1))/nd
                c1 = tmp + (c1*x*(2*nd - 1))/nd
        return c0 + c1*x
    
    
    
    
    
    
    
    
