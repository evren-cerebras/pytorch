from __future__ import absolute_import, division, print_function, unicode_literals

import hashlib
import torch
import torch.fx
from typing import Dict, Any, TYPE_CHECKING
from torch.fx.node import _get_qualified_name, _format_arg
from torch.fx.passes.shape_prop import TensorMetadata
from torch.fx._compatibility import compatibility
from itertools import chain

try:
    import pydot
    HAS_PYDOT = True
except ImportError:
    HAS_PYDOT = False

_COLOR_MAP = {
    "placeholder": '"AliceBlue"',
    "call_module": "LemonChiffon1",
    "get_param": "Yellow2",
    "get_attr": "LightGrey",
    "output": "PowderBlue",
}

_HASH_COLOR_MAP = [
    "CadetBlue1",
    "Coral",
    "DarkOliveGreen1",
    "DarkSeaGreen1",
    "GhostWhite",
    "Khaki1",
    "LavenderBlush1",
    "LightSkyBlue",
    "MistyRose1",
    "MistyRose2",
    "PaleTurquoise2",
    "PeachPuff1",
    "Salmon",
    "Thistle1",
    "Thistle3",
    "Wheat1",
]

_WEIGHT_TEMPLATE = {
    "shape": "record",
    "fillcolor": "Salmon",
    "style": '"filled,rounded"',
    "fontcolor": "#000000",
}

if HAS_PYDOT:
    @compatibility(is_backward_compatible=False)
    class FxGraphDrawer:
        """
        Visualize a torch.fx.Graph with graphviz
        Basic usage:
            g = FxGraphDrawer(symbolic_traced, "resnet18")
            with open("a.svg", "w") as f:
                f.write(g.get_dot_graph().create_svg())
        """

        def __init__(self, graph_module: torch.fx.GraphModule, name: str, ignore_getattr: bool = False):
            self._name = name
            self._dot_graphs = {name: self._to_dot(graph_module, name, ignore_getattr)}

            for node in graph_module.graph.nodes:
                if node.op != "call_module":
                    continue

                leaf_node = self._get_leaf_node(graph_module, node)

                if not isinstance(leaf_node, torch.fx.GraphModule):
                    continue

                self._dot_graphs[f"{name}_{node.target}"] = self._to_dot(leaf_node, f"{name}_{node.target}", ignore_getattr)

        def get_dot_graph(self, submod_name=None) -> pydot.Dot:
            if submod_name is None:
                return self.get_main_dot_graph()
            else:
                return self.get_submod_dot_graph(submod_name)

        def get_main_dot_graph(self) -> pydot.Dot:
            return self._dot_graphs[self._name]

        def get_submod_dot_graph(self, submod_name) -> pydot.Dot:
            return self._dot_graphs[f"{self._name}_{submod_name}"]

        def get_all_dot_graphs(self) -> Dict[str, pydot.Dot]:
            return self._dot_graphs

        def _get_node_style(self, node: torch.fx.Node) -> Dict[str, str]:
            template = {
                "shape": "record",
                "fillcolor": "#CAFFE3",
                "style": '"filled,rounded"',
                "fontcolor": "#000000",
            }
            if node.op in _COLOR_MAP:
                template["fillcolor"] = _COLOR_MAP[node.op]
            else:
                # Use a random color for each node; based on its name so it's stable.
                target_name = node._pretty_print_target(node.target)
                target_hash = int(hashlib.md5(target_name.encode()).hexdigest()[:8], 16)
                template["fillcolor"] = _HASH_COLOR_MAP[target_hash % len(_HASH_COLOR_MAP)]
            return template

        def _get_leaf_node(
            self, module: torch.nn.Module, node: torch.fx.Node
        ) -> torch.nn.Module:
            py_obj = module
            assert isinstance(node.target, str)
            atoms = node.target.split(".")
            for atom in atoms:
                if not hasattr(py_obj, atom):
                    raise RuntimeError(
                        str(py_obj) + " does not have attribute " + atom + "!"
                    )
                py_obj = getattr(py_obj, atom)
            return py_obj

        def _typename(self, target: Any) -> str:
            if isinstance(target, torch.nn.Module):
                return torch.typename(target)

            if isinstance(target, str):
                return target

            return _get_qualified_name(target)

        def _get_node_label(self, module: torch.fx.GraphModule, node: torch.fx.Node) -> str:
            def _get_str_for_args_kwargs(arg):
                if isinstance(arg, tuple):
                    s = r",\n".join(_format_arg(a, max_list_len=10) for a in arg)
                    return fr"(\l{s},\n)".replace("{", r"\{").replace("}", r"\}")
                if isinstance(arg, dict):
                    s = r",\n".join(f"{k}: {_format_arg(v, max_list_len=10)}" for k, v in arg.items())
                    return fr"{{\l{s},\n}}".replace("{", r"\{").replace("}", r"\}")
                return _format_arg(arg).replace("{", r"\{").replace("}", r"\}")


            label = "{" + f"name=%{node.name}|op_code={node.op}\n"

            if node.op == "call_module":
                leaf_module = self._get_leaf_node(module, node)
                label += r"\n" + self._typename(leaf_module) + r"\n|"
                extra = ""
                if hasattr(leaf_module, "__constants__"):
                    extra = r"\n".join(
                        [f"{c}: {getattr(leaf_module, c)}" for c in leaf_module.__constants__]  # type: ignore[union-attr]
                    )
                label += extra + r"\n"
            else:
                label += f"|target={self._typename(node.target)}" + r"\n"
                if len(node.args) > 0:
                    label += f"|args={_get_str_for_args_kwargs(node.args)}" + r"\l"
                if len(node.kwargs) > 0:
                    label += f"|kwargs={_get_str_for_args_kwargs(node.kwargs)}" + r"\l"
                label += f"|num_users={len(node.users)}" + r"\n"

            tensor_meta = node.meta.get('tensor_meta')
            label += self._tensor_meta_to_label(tensor_meta)

            return label + "}"

        def _tensor_meta_to_label(self, tm) -> str:
            if tm is None:
                return ""
            elif isinstance(tm, TensorMetadata):
                return self._stringify_tensor_meta(tm)
            elif isinstance(tm, list):
                result = ""
                for item in tm:
                    result += self._tensor_meta_to_label(item)
                return result
            elif isinstance(tm, dict):
                result = ""
                for k, v in tm.items():
                    result += self._tensor_meta_to_label(v)
                return result
            elif isinstance(tm, tuple):
                result = ""
                for item in tm:
                    result += self._tensor_meta_to_label(item)
                return result
            else:
                raise RuntimeError(f"Unsupported tensor meta type {type(tm)}")

        def _stringify_tensor_meta(self, tm: TensorMetadata) -> str:
            result = ""
            if not hasattr(tm, "dtype"):
                print("tm", tm)
            result += "|" + "dtype" + "=" + str(tm.dtype) + r"\n"
            result += "|" + "shape" + "=" + str(tuple(tm.shape)) + r"\n"
            result += "|" + "requires_grad" + "=" + str(tm.requires_grad) + r"\n"
            result += "|" + "stride" + "=" + str(tm.stride) + r"\n"
            if tm.is_quantized:
                assert tm.qparams is not None
                assert "qscheme" in tm.qparams
                qscheme = tm.qparams["qscheme"]
                if qscheme in {
                        torch.per_tensor_affine,
                        torch.per_tensor_symmetric,
                }:
                    result += "|" + "q_scale" + "=" + str(tm.qparams["scale"]) + r"\n"
                    result += "|" + "q_zero_point" + "=" + str(tm.qparams["zero_point"]) + r"\n"
                elif qscheme in {
                        torch.per_channel_affine,
                        torch.per_channel_symmetric,
                        torch.per_channel_affine_float_qparams,
                }:
                    result += "|" + "q_per_channel_scale" + "=" + str(tm.qparams["scale"]) + r"\n"
                    result += "|" + "q_per_channel_zero_point" + "=" + str(tm.qparams["zero_point"]) + r"\n"
                    result += "|" + "q_per_channel_axis" + "=" + str(tm.qparams["axis"]) + r"\n"
                else:
                    raise RuntimeError(f"Unsupported qscheme: {qscheme}")
                result += "|" + "qscheme" + "=" + str(tm.qparams["qscheme"]) + r"\n"
            return result

        def _get_tensor_label(self, t: torch.Tensor) -> str:
            return str(t.dtype) + str(list(t.shape)) + r"\n"

        def _to_dot(self, graph_module: torch.fx.GraphModule, name: str, ignore_getattr: bool) -> pydot.Dot:
            """
            Actual interface to visualize a fx.Graph. Note that it takes in the GraphModule instead of the Graph
            """
            dot_graph = pydot.Dot(name, rankdir="TB")

            for node in graph_module.graph.nodes:
                if ignore_getattr and node.op == "get_attr":
                    continue

                style = self._get_node_style(node)
                dot_node = pydot.Node(
                    node.name, label=self._get_node_label(graph_module, node), **style
                )
                dot_graph.add_node(dot_node)

                def get_module_params_or_buffers():
                    for pname, ptensor in chain(
                        leaf_module.named_parameters(), leaf_module.named_buffers()
                    ):
                        pname1 = node.name + "." + pname
                        label1 = (
                            pname1 + "|op_code=get_" + "parameter"
                            if isinstance(ptensor, torch.nn.Parameter)
                            else "buffer" + r"\l"
                        )
                        dot_w_node = pydot.Node(
                            pname1,
                            label="{" + label1 + self._get_tensor_label(ptensor) + "}",
                            **_WEIGHT_TEMPLATE,
                        )
                        dot_graph.add_node(dot_w_node)
                        dot_graph.add_edge(pydot.Edge(pname1, node.name))

                if node.op == "call_module":
                    leaf_module = self._get_leaf_node(graph_module, node)

                    if not isinstance(leaf_module, torch.fx.GraphModule):
                        get_module_params_or_buffers()

            for node in graph_module.graph.nodes:
                if ignore_getattr and node.op == "get_attr":
                    continue

                for user in node.users:
                    dot_graph.add_edge(pydot.Edge(node.name, user.name))

            return dot_graph

else:
    if not TYPE_CHECKING:
        @compatibility(is_backward_compatible=False)
        class FxGraphDrawer:
            def __init__(self, graph_module: torch.fx.GraphModule, name: str, ignore_getattr: bool = False):
                raise RuntimeError('FXGraphDrawer requires the pydot package to be installed. Please install '
                                   'pydot through your favorite Python package manager.')
