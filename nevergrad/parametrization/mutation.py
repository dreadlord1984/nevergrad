# Copyright (c) Facebook, Inc. and its affiliates. All Rights Reserved.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

import typing as tp
import numpy as np
from . import core
from .import transforms
from .data import Mutation as Mutation
from .data import Array
from .choice import Choice


class Crossover(Mutation):
    """Operator for merging part of an array into another one

    Parameters
    ----------
    axis: None or int or tuple of ints
        the axis (or axes) on which the merge will happen. This axis will be split into 3 parts: the first and last one will take
        value from the first array, the middle one from the second array.
    max_size: None or int
        maximum size of the part taken from the second array. By default, this is at most around half the number of total elements of the
        array to the power of 1/number of axis.


    Notes
    -----
    - this is experimental, the API may evolve
    - when using several axis, the size of the second array part is the same on each axis (aka a square in 2D, a cube in 3D, ...)

    Examples:
    ---------
    - 2-dimensional array, with crossover on dimension 1:
      0 1 0 0
      0 1 0 0
      0 1 0 0
    - 2-dimensional array, with crossover on dimensions 0 and 1:
      0 0 0 0
      0 1 1 0
      0 1 1 0
    """

    def __init__(
        self,
        axis: tp.Optional[tp.Union[int, tp.Iterable[int]]] = None,
        max_size: tp.Optional[int] = None,
        fft: bool = False
    ) -> None:
        if not isinstance(axis, core.Parameter):
            axis = (axis,) if isinstance(axis, int) else tuple(axis) if axis is not None else None
        super().__init__(max_size=max_size, axis=axis, fft=fft)

    @property
    def axis(self) -> tp.Optional[tp.Tuple[int, ...]]:
        return self.parameters["axis"].value  # type: ignore

    def apply(self, arrays: tp.Sequence["Array"]) -> None:
        new_value = self._apply_array([a._value for a in arrays])
        bounds = arrays[0].bounds
        if self.parameters["fft"].value and any(x is not None for x in bounds):
            new_value = transforms.Clipping(a_min=bounds[0], a_max=bounds[1]).forward(new_value)
        arrays[0].value = new_value

    def _apply_array(self, arrays: tp.Sequence[np.ndarray]) -> np.ndarray:
        # checks
        if len(arrays) != 2:
            raise Exception("Crossover can only be applied between 2 individuals")
        transf = transforms.Fourrier(range(arrays[0].dim) if self.axis is None else self.axis) if self.parameters["fft"].value else None
        if transf is not None:
            arrays = [transf.forward(a) for a in arrays]
        shape = arrays[0].shape
        assert shape == arrays[1].shape, "Individuals should have the same shape"
        # settings
        axis = tuple(range(len(shape))) if self.axis is None else self.axis
        max_size = self.parameters["max_size"].value
        max_size = int(((arrays[0].size + 1) / 2)**(1 / len(axis))) if max_size is None else max_size
        max_size = min(max_size, *(shape[a] - 1 for a in axis))
        size = 1 if max_size == 1 else self.random_state.randint(1, max_size)
        # slices
        slices = _make_slices(shape, axis, size, self.random_state)
        result = np.array(arrays[0], copy=True)
        result[tuple(slices)] = arrays[1][tuple(slices)]
        if transf is not None:
            result = transf.backward(result)
        return result


def _make_slices(
    shape: tp.Tuple[int, ...],
    axes: tp.Tuple[int, ...],
    size: int,
    rng: np.random.RandomState
) -> tp.List[slice]:
    slices = []
    for a, s in enumerate(shape):
        if a in axes:
            if s <= 1:
                raise ValueError("Cannot crossover on axis with size 1")
            start = rng.randint(s - size)
            slices.append(slice(start, start + size))
        else:
            slices.append(slice(0, s))
    return slices


class Translation(Mutation):

    def __init__(self, axis: tp.Optional[tp.Union[int, tp.Iterable[int]]] = None):
        if not isinstance(axis, core.Parameter):
            axis = (axis,) if isinstance(axis, int) else tuple(axis) if axis is not None else None
        super().__init__(axis=axis)

    @property
    def axis(self) -> tp.Optional[tp.Tuple[int, ...]]:
        return self.parameters["axis"].value  # type: ignore

    def _apply_array(self, arrays: tp.Sequence[np.ndarray]) -> np.ndarray:
        assert len(arrays) == 1
        data = arrays[0]
        axis = tuple(range(data.dim)) if self.axis is None else self.axis
        shifts = [self.random_state.randint(data.shape[a]) for a in axis]
        return np.roll(data, shifts, axis=axis)  # type: ignore


class LocalGaussian(Mutation):

    def __init__(self, size: tp.Union[int, core.Parameter], axes: tp.Optional[tp.Union[int, tp.Iterable[int]]] = None):
        if not isinstance(axes, core.Parameter):
            axes = (axes,) if isinstance(axes, int) else tuple(axes) if axes is not None else None
        super().__init__(axes=axes, size=size)

    @property
    def axes(self) -> tp.Optional[tp.Tuple[int, ...]]:
        return self.parameters["axes"].value  # type: ignore

    def apply(self, arrays: tp.Sequence[Array]) -> None:
        arrays = list(arrays)
        assert len(arrays) == 1
        data = np.zeros(arrays[0].value.shape)
        # settings
        axis = tuple(range(len(data.shape))) if self.axes is None else self.axes
        size = self.parameters["size"].value
        # slices
        slices = _make_slices(data.shape, axis, size, self.random_state)
        shape = data[tuple(slices)].shape
        data[tuple(slices)] += self.random_state.normal(0, 1, size=shape)
        arrays[0]._internal_set_standardized_data(data.ravel(), reference=arrays[0])


class TunedTranslation(Mutation):

    def __init__(self, axis: int, shape: tp.Sequence[int]):
        assert isinstance(axis, int)
        self.shape = tuple(shape)
        super().__init__(shift=Choice(range(1, shape[axis])))
        self.axis = axis

    @property
    def shift(self) -> Choice:
        return self.parameters["shift"]  # type: ignore

    def _apply_array(self, arrays: tp.Sequence[np.ndarray]) -> np.ndarray:
        assert len(arrays) == 1
        data = arrays[0]
        assert data.shape == self.shape
        shift = self.shift.value
        # update shift arrray
        shifts = self.shift.weights.value
        self.shift.weights.value = np.roll(shifts, shift)  # update probas
        return np.roll(data, shift, axis=self.axis)  # type: ignore

    def _internal_spawn_child(self) -> "TunedTranslation":
        child = self.__class__(axis=self.axis, shape=self.shape)
        child.parameters._content = {k: v.spawn_child() if isinstance(v, core.Parameter) else v
                                     for k, v in self.parameters._content.items()}
        return child