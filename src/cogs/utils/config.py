from __future__ import annotations

import os
import typing
import yaml

located = '/'.join(os.path.abspath(os.path.dirname(__file__)).split('/')[:-3]) + '/config.yaml'

f = open(located, 'r')

Config: typing.Callable[[], dict[str, str | bool | list[str] | dict[str, str | bool]]] = lambda: yaml.safe_load(f)