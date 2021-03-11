# Copyright 2018-2020 Xanadu Quantum Technologies Inc.

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
This module contains the autograd wrappers :class:`grad` and :func:`jacobian`
"""
import numpy as _np

from autograd.core import make_vjp as _make_vjp
from autograd.wrap_util import unary_to_nary
from autograd.extend import vspace
from autograd import jacobian as _jacobian

make_vjp = unary_to_nary(_make_vjp)


class grad:
    """Returns the gradient as a callable function of (functions of) QNodes.

    Function arguments with the property ``requires_grad`` set to ``False``
    will automatically be excluded from the gradient computation, unless
    the ``argnum`` keyword argument is passed.

    When the output gradient function is executed, both the forward pass
    *and* the backward pass will be performed in order to
    compute the gradient. The value of the forward pass is available via the
    :attr:`~.forward` property.

    Args:
        func (function): a plain QNode, or a Python function that contains
            a combination of quantum and classical nodes
        argnum (int, list(int), None): Which argument(s) to take the gradient
            with respect to. By default, the arguments themselves are used
            to determine differentiability, by examining the ``requires_grad``
            property. Providing this keyword argument overrides this behaviour,
            allowing argument differentiability to be defined manually for the returned gradient function.

    Returns:
        function: The function that returns the gradient of the input
        function with respect to the differentiable arguments, or, if specified,
        the arguments in ``argnum``.
    """

    def __init__(self, fun, argnum=None):
        self._forward = None
        self._grad_fn = None

        self._fun = fun
        self._argnum = argnum

        if self._argnum is not None:
            # If the differentiable argnum is provided, we can construct
            # the gradient function at once during initialization
            self._grad_fn = self._grad_with_forward(fun, argnum=argnum)

    def _get_grad_fn(self, args):
        """Get the required gradient function.

        * If the differentiable argnum was provided on initialization,
          this has been pre-computed and is available via self._grad_fn

        * Otherwise, we must dynamically construct the gradient function by
          inspecting as to which of the parameter arguments are marked
          as differentiable.
        """
        if self._grad_fn is not None:
            return self._grad_fn

        # Inspect the arguments for differentiability, and
        # compute the autograd gradient function with required argnums
        # dynamically.
        argnum = []

        for idx, arg in enumerate(args):
            if getattr(arg, "requires_grad", True):
                argnum.append(idx)

        if len(argnum) == 1:
            argnum = argnum[0]

        return self._grad_with_forward(
            self._fun,
            argnum=argnum,
        )

    def __call__(self, *args, **kwargs):
        """Evaluates the gradient function, and saves the function value
        calculated during the forward pass in :attr:`.forward`."""
        grad_value, ans = self._get_grad_fn(args)(*args, **kwargs)
        self._forward = ans
        return grad_value

    @property
    def forward(self):
        """float: The result of the forward pass calculated while performing
        backpropagation. Will return ``None`` if the backpropagation has not yet
        been performed."""
        return self._forward

    @staticmethod
    @unary_to_nary
    def _grad_with_forward(fun, x):
        """This function is a replica of ``autograd.grad``, with the only
        difference being that it returns both the gradient *and* the forward pass
        value."""
        vjp, ans = _make_vjp(fun, x)

        if not vspace(ans).size == 1:
            raise TypeError(
                "Grad only applies to real scalar-output functions. "
                "Try jacobian, elementwise_grad or holomorphic_grad."
            )

        grad_value = vjp(vspace(ans).ones())
        return grad_value, ans


def jacobian(func, argnum=None):
    """Returns the Jacobian as a callable function of vector-valued
    (functions of) QNodes.

    This is a wrapper around the :mod:`autograd.jacobian` function.

    Args:
        func (function): A vector-valued Python function or QNode that contains
            a combination of quantum and classical nodes. The output of the computation
            must consist of a single NumPy array (if classical) or a tuple of
            expectation values (if a quantum node)
        argnum (int or Sequence[int]): Which argument to take the gradient
            with respect to. If a sequence is given, the Jacobian matrix
            corresponding to all input elements and all output elements is returned.

    Returns:
        function: the function that returns the Jacobian of the input
        function with respect to the arguments in argnum
    """
    # pylint: disable=no-value-for-parameter

    if argnum is not None:
        # for backwards compatibility with existing code
        # that manually specifies argnum
        if isinstance(argnum, int):
            return _jacobian(func, argnum)

        return lambda *args, **kwargs: _np.stack(
            [_jacobian(func, arg)(*args, **kwargs) for arg in argnum]
        ).T

    def _jacobian_function(*args, **kwargs):
        """Inspect the arguments for differentiability, and
        compute the autograd gradient function with required argnums
        dynamically.

        This wrapper function is returned to the user instead of autograd.jacobian,
        so that we can take into account cases where the user computes the
        jacobian function once, but then calls it with arguments that change
        in differentiability.
        """
        argnum = []

        for idx, arg in enumerate(args):
            if getattr(arg, "requires_grad", True):
                argnum.append(idx)

        if not argnum:
            return tuple()

        if len(argnum) == 1:
            return _jacobian(func, argnum[0])(*args, **kwargs)

        return _np.stack([_jacobian(func, arg)(*args, **kwargs) for arg in argnum]).T

    return _jacobian_function


def _fd_first_order_centered(f, argnum, delta, *args, idx=None, **kwargs):

    r"""Uses a central finite difference approximation to compute the gradient
    of the function ``f`` with respect to the argument ``argnum``.

    .. math::

        \frac{\partial f(x)}{\partial x_i} \approx \frac{f(x_i + \delta/2)
        - f(x_i - \delta/2)}{\delta}

    Args:
        f (function): function with signature ``f(*args, **kwargs)``
        argnum (int): which argument to take a gradient with respect to
        delta (float): step size used to evaluate the finite difference
        idx (list[int]): if argument ``args[argnum]`` is an array, ``idx`` specifies
            the indices of the arguments to differentiate

    Returns:
        (float or array): the gradient of the function ``f``
    """

    if argnum > len(args) - 1:
        raise ValueError(
            "The value of 'argnum' has to be between 0 and {}; got {}".format(len(args) - 1, argnum)
        )

    if delta <= 0.0:
        raise ValueError(
            "The value of the step size 'delta' has to be greater than 0; got {}".format(delta)
        )

    x = _np.array(args[argnum])
    gradient = _np.zeros_like(x, dtype="O")

    if idx is None:
        idx = list(_np.ndindex(*x.shape))
    else:
        bounds = _np.array(x.shape) - 1
        for _idx in idx:

            if len(_idx) != x.ndim:
                raise ValueError(
                    "Elements of 'idx' must be of lenght {}; got element {} with lenght {}".format(
                        x.ndim, _idx, len(_idx)
                    )
                )

            if (_np.array(_idx) > bounds).any():
                raise ValueError(
                    "Indices in 'idx' can not be greater than {}; got {}".format(
                        tuple(bounds), _idx
                    )
                )

    for i in idx:
        shift = _np.zeros_like(x)
        shift[i] += 0.5 * delta
        gradient[i] = (
            f(*args[:argnum], x + shift, *args[argnum + 1 :], **kwargs)
            - f(*args[:argnum], x - shift, *args[argnum + 1 :], **kwargs)
        ) * delta ** -1

    return gradient


def _fd_second_order_centered(f, argnum, delta, *args, idx=None, **kwargs):
    r"""Uses a central finite difference approximation to compute the second-order
    derivative :math:`\frac{\partial^2 f(x)}{\partial x_i \partial x_j}` of the function ``f``
    with respect to the argument ``argnum``.

    Args:
        f (function): function with signature ``f(*args, **kwargs)``
        argnum (int): which argument to take the derivative with respect to
        delta (float): step size used to evaluate the finite differences
        idx (list[int]): if argument ``args[argnum]`` is an array, `idx`` specifies
            the indices ``i, j`` of the arguments to differentiate

    Returns:
        (float): the second-order derivative of the function ``f``
    """

    if argnum > len(args) - 1:
        raise ValueError(
            "The value of 'argnum' has to be between 0 and {}; got {}".format(len(args) - 1, argnum)
        )

    if delta <= 0.0:
        raise ValueError(
            "The value of the step size 'delta' has to be greater than 0; got {}".format(delta)
        )

    x = _np.array(args[argnum])

    if idx is None:
        idx = [(), ()]
    else:
        if len(idx) > 2:
            raise ValueError(
                "The number of indices in 'idx' can not be greater than 2; got {} indices".format(
                    len(idx)
                )
            )

        bounds = _np.array(x.shape) - 1
        for _idx in idx:

            if len(_idx) != x.ndim:
                raise ValueError(
                    "Elements of 'idx' must be of lenght {}; got element {} with lenght {}".format(
                        x.ndim, _idx, len(_idx)
                    )
                )

            if (_np.array(_idx) > bounds).any():
                raise ValueError(
                    "Indices in 'idx' can not be greater than {}; got {}".format(
                        tuple(bounds), _idx
                    )
                )

    i, j = idx

    # diagonal
    if i == j:
        shift = _np.zeros_like(x)
        shift[i] += delta
        deriv2 = (
            f(*args[:argnum], x + shift, *args[argnum + 1 :], **kwargs)
            - 2 * f(*args[:argnum], x, *args[argnum + 1 :], **kwargs)
            + f(*args[:argnum], x - shift, *args[argnum + 1 :], **kwargs)
        ) * delta ** -2

    # off-diagonal
    if i != j:
        shift_i = _np.zeros_like(x)
        shift_i[i] += 0.5 * delta

        shift_j = _np.zeros_like(x)
        shift_j[j] += 0.5 * delta

        deriv2 = (
            f(*args[:argnum], x + shift_i + shift_j, *args[argnum + 1 :], **kwargs)
            - f(*args[:argnum], x - shift_i + shift_j, *args[argnum + 1 :], **kwargs)
            - f(*args[:argnum], x + shift_i - shift_j, *args[argnum + 1 :], **kwargs)
            + f(*args[:argnum], x - shift_i - shift_j, *args[argnum + 1 :], **kwargs)
        ) * delta ** -2

    return deriv2


def finite_diff(F, x, i=None, delta=0.01):
    r"""Uses a central finite difference approximation to evaluate the derivative
    :math:`\frac{\partial F(x)}{\partial x_i}` of the function ``F(x)``
    at point ``x``.

    .. math::

        \frac{\partial F(x)}{\partial x_i} \approx \frac{F(x_i + \delta/2)
        - F(x_i - \delta/2)}{\delta}

    Args:
        F (callable): function with signature ``F(x)``
        x (float or array[float]): single-value or 1D array with the values of the variable ``x``
        i (int): index denoting the variable ``x_i`` with respect to which the derivative is calculated
        delta (float): Step size used to evaluate the finite difference

    Returns:
        (any): the derivative :math:`\frac{\partial F(x)}{\partial x_i}` of the
        function ``F`` at point ``x``. The output of the function is the same type as
        the output of ``F(x)``.

    **Examples**

    >>> def g(x):
    ...     return np.array([np.sin(x), 1/x])
    >>> x = -0.25
    >>> print(finite_diff(g, x))
    [0.96890838 -16.00640256]

    >>> def H(x):
    ...     return qml.qchem.molecular_hamiltonian(['H', 'H'], x)[0]
    >>> x = np.array([0.0, 0.0, -0.66140414, 0.0, 0.0, 0.66140414])
    >>> print(finite_diff(H, x, i=2))
    (0.7763135746699901) [I0]
    + (0.0853436084402831) [Z0]
    + (0.0853436084402831) [Z1]
    + (-0.2669341093715999) [Z2]
    + (-0.2669341093715999) [Z3]
    + (0.02523362875533064) [Z0 Z1]
    + (-0.007216244399306515) [Y0 X1 X2 Y3]
    + (0.007216244399306515) [Y0 Y1 X2 X3]
    + (0.007216244399306515) [X0 X1 Y2 Y3]
    + (-0.007216244399306515) [X0 Y1 Y2 X3]
    + (0.030654287758868914) [Z0 Z2]
    + (0.02343804335956101) [Z0 Z3]
    + (0.02343804335956101) [Z1 Z2]
    + (0.030654287758868914) [Z1 Z3]
    + (0.024944077874217152) [Z2 Z3]
    """

    if not callable(F):
        error_message = "{} object is not callable. \n" "'F' should be a callable function".format(
            type(F)
        )
        raise TypeError(error_message)

    if isinstance(x, _np.ndarray):
        if i is None or i not in range(0, x.size):
            raise ValueError(
                "'i' must be an integer between {} and {}; got {}".format(0, x.size - 1, i)
            )
        d = _np.zeros_like(x)
        d[i] = 0.5 * delta
    else:
        d = delta * 0.5

    return (F(x + d) - F(x - d)) * delta ** -1
