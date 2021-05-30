# Copyright 2019-2020 Xanadu Quantum Technologies Inc.

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import strawberryfields.program_utils as pu
import networkx as nx

from .fock import Fock
from .gaussian_unitary import GaussianUnitary

class GaussianMerge(Fock):
    """Strict compiler for the X class of circuits.

    Ensures that the program exactly matches the devices topology.
    As a result, this compiler only accepts :class:`~.ops.S2gate`, :class:`~.ops.MZgate`,
    :class:`~.ops.Rgate`, and :class:`~.ops.MeasureFock` operations.

    This compiler must be used with an X series device specification.

    **Example**

    >>> eng = sf.RemoteEngine("X8")
    >>> spec = eng.device_spec
    >>> prog.compile(device=spec, compiler="GaussianMerge")
    """

    short_name = "gaussian_merge"
    primitives = {
        # meta operations
        "All",
        "_New_modes",
        "_Delete",
        # state preparations
        "Vacuum",
        "Coherent",
        "Squeezed",
        "DisplacedSqueezed",
        "Thermal",
        "Fock",
        "Catstate",
        "Ket",
        "DensityMatrix",
        # measurements
        "MeasureFock",
        "MeasureHomodyne",
        # channels
        "LossChannel",
        # single mode gates
        "Dgate",
        "Sgate",
        "Rgate",
        "Vgate",
        "Kgate",
        # two mode gates
        "BSgate",
        "CKgate",
        "S2gate",
    }

    gaussian_ops = ["Dgate",
                    "BSgate",
                    "S2gate",
                    "Sgate",
                    "GaussianTransform",
                    "Rgate",
                    "Interferometer",
                    "MZgate"]

    decompositions =  {
        "GraphEmbed": {},
        "BipartiteGraphEmbed": {},
        "Gaussian": {},
        "Pgate": {},
        "CXgate": {},
        "CZgate": {},
        "Xgate": {},
        "Zgate": {},
        "GKP": {},
        "Fouriergate": {},}

    def compile(self, seq, registers):
        self.curr_seq = seq
        while self.merge_a_gaussian_op(registers):
            continue
        return self.curr_seq


    def merge_a_gaussian_op(self, registers):
        self.DAG = pu.list_to_DAG(self.curr_seq)

        for op in list(self.DAG.nodes):
            successors = list(self.DAG.successors(op))
            predecessors = list(self.DAG.predecessors(op))
            # If operation is a Gaussian operation
            if self.get_op_name(op) in self.gaussian_ops:
                merged_gaussian_ops = self.get_valid_gaussian_merge_ops(op)

                # If there are successor operations that are Gaussian and can be merged
                if merged_gaussian_ops:
                    self.new_DAG = self.DAG.copy()
                    # Fix order of operations
                    merged_gaussian_ops = self.organize_merge_ops([op] + merged_gaussian_ops)
                    gaussian_transform = GaussianUnitary().compile(merged_gaussian_ops, registers)
                    self.new_DAG.add_node(gaussian_transform[0])

                    # Logic to add displacement gates. Returns dictionary,
                    # where the value is a displacement gate added and its key is the qumode its operating upon.
                    displacement_mapping = self.add_displacement_gates(gaussian_transform, merged_gaussian_ops)

                    # If there are predecessors: Attach predecessor edges to new gaussian transform
                    if predecessors:
                        self.new_DAG.add_edges_from([(pre, gaussian_transform[0]) for pre in predecessors])

                    # Add edges to all successor operations not merged
                    self.add_non_gaussian_successor_gates(gaussian_transform, successors, displacement_mapping)

                    # Add edges for all successor/predecessor operations of the merged operations
                    self.add_gaussian_pre_and_succ_gates(gaussian_transform, merged_gaussian_ops, displacement_mapping)

                    self.new_DAG.remove_nodes_from([op] + merged_gaussian_ops)

                    self.curr_seq = pu.DAG_to_list(self.new_DAG)
                    return True
        return False

    def recursive_d_gate_successors(self, gate):
        d_gates = []
        successors = list(self.DAG.successors(gate))
        if successors is None:
            return d_gates
        for successor in successors:
            if "Dgate" in self.get_op_name(successor):
                d_gates.append(successor)
                ret = self.recursive_d_gate_successors(successor)
                if ret:
                    d_gates += ret
        return d_gates

    def add_non_gaussian_successor_gates(self, gaussian_transform, successors, displacement_mapping):
        for successor_op in successors:
            if self.get_op_name(successor_op) not in self.gaussian_ops:
                # If there are displacement gates.
                # Add edges from it to successor gates if they act upon the same qumodes
                if displacement_mapping:
                    placed_edge = False
                    # Get qumodes acted upon by successor gate. And add edges if dependancy exists
                    qumodes_operated_upon = self.get_qumodes_operated_upon(successor_op)
                    for qumode in qumodes_operated_upon:
                        if qumode in displacement_mapping:
                            self.new_DAG.add_edge(displacement_mapping[qumode], successor_op)
                            placed_edge = True
                    # If successor gate does not act on qumodes that the displacement gates act upon:
                    # Add edge from gaussian transform to successor operation
                    if not placed_edge:
                        self.new_DAG.add_edge(gaussian_transform[0], successor_op)
                else:
                    self.new_DAG.add_edge(gaussian_transform[0], successor_op)

    def add_gaussian_pre_and_succ_gates(self, gaussian_transform, merged_gaussian_ops, displacement_mapping):
        successor_operations_added = []
        for gaussian_op in merged_gaussian_ops:
            # Need special logic if there are displacement gates
            if displacement_mapping:
                for successor_op in self.DAG.successors(gaussian_op):
                    placed_edge = False
                    successor_op_qumodes = self.get_qumodes_operated_upon(successor_op)
                    for qumode in successor_op_qumodes:
                        # If displacement gate operates on the same qumodes as the non-gaussian operation then dont add edge
                        # If register operated upon by successor operation has a displacement gate. Add edge.
                        if qumode in displacement_mapping and \
                            qumode not in self.non_gaussian_qumodes_dependecy(successor_op):
                                self.new_DAG.add_edge(displacement_mapping[qumode], successor_op)
                                placed_edge = True

                    if not placed_edge:
                        self.new_DAG.add_edge(gaussian_transform[0], successor_op)
                    successor_operations_added.append(successor_op)
            else:
                self.new_DAG.add_edges_from([(gaussian_transform[-1], post) for post in self.DAG.successors(gaussian_op)])
                successor_operations_added += [post for post in self.DAG.successors(gaussian_op)]

        for gaussian_op in merged_gaussian_ops:
            # Append Predecessors to Gaussian Transform
            for predecessor in self.DAG.predecessors(gaussian_op):
                # Make sure adding the edge wont make a cycle
                if predecessor not in successor_operations_added:
                    self.new_DAG.add_edge(predecessor, gaussian_transform[0])

    def add_displacement_gates(self, gaussian_transform, merged_gaussian_ops):
        displacement_mapping = {}
        if len(gaussian_transform) > 1:
            for idx, displacement_gate in enumerate(gaussian_transform[1:]):
                self.new_DAG.add_node(displacement_gate)
                self.new_DAG.add_edge(gaussian_transform[0], displacement_gate)
                # NOTE: Assumes all displacement gates are single gate displacement gates
                displacement_mapping[displacement_gate.reg[0].ind] = displacement_gate
                if displacement_gate in merged_gaussian_ops:
                    merged_gaussian_ops.remove(displacement_gate)
        return displacement_mapping

    def get_valid_gaussian_merge_ops(self, op):
        merged_gaussian_ops = []
        non_gauss_op_qumodes = []

        for successor_op in self.DAG.successors(op):
            if "Dgate" in self.get_op_name(successor_op):
                x = 10
            # If successor operation is a Gaussian operation append to list for merging
            if self.get_op_name(successor_op) in self.gaussian_ops:
                merged_gaussian_ops.append(successor_op)
                # Get displacement operations (recursively) that follow after successor operation
                d_gate_successors = self.recursive_d_gate_successors(successor_op)
                if d_gate_successors:
                    merged_gaussian_ops += d_gate_successors

        op_qumodes = self.get_qumodes_operated_upon(op)
        for gaussian_op in merged_gaussian_ops:
            if any(qumode in op_qumodes for qumode in self.non_gaussian_qumodes_dependecy(gaussian_op)):
                # Cannot merge gaussian ops that are operated upon a non-gaussian gate beforehand
                # E.x. BS | q[0],q[1] has successors V | q[1] & S2gate q[1], q[2]
                merged_gaussian_ops.remove(gaussian_op)

        for gaussian_op in merged_gaussian_ops:
            for predecessor in self.DAG.predecessors(gaussian_op):
                if predecessor is op:
                    continue
                if predecessor not in merged_gaussian_ops and self.get_op_name(predecessor) in self.gaussian_ops:
                    merged_gaussian_ops.append(predecessor)


        # Merging Gaussian Transforms and Displacement gates does nothing
        if "GaussianTransform" in self.get_op_name(op):
            all_displacement_gates = True
            for gate in merged_gaussian_ops:
                if "Dgate" not in self.get_op_name(gate):
                    all_displacement_gates = False
            if all_displacement_gates:
                return []

        new_merge_ops = self.remove_far_merge_ops(op, merged_gaussian_ops)
        return merged_gaussian_ops

    def get_op_name(self, op):
        return op.op.__class__.__name__

    def non_gaussian_qumodes_dependecy(self, op):
        for predecessor in self.DAG.predecessors(op):
            if self.get_op_name(predecessor) not in self.gaussian_ops:
                return self.get_qumodes_operated_upon(predecessor)
        return []

    def get_qumodes_operated_upon(self, op):
        return [reg.ind for reg in op.reg]

    def remove_far_merge_ops(self, op, merged_gaussian_ops):
        op_qumodes = self.get_qumodes_operated_upon(op)
        new_merged_gaussian_ops = merged_gaussian_ops.copy()
        for gaussian_op in merged_gaussian_ops:
            remove_op = False
            pre_ops = self.DAG.predecessors(gaussian_op)
            for pre_op in pre_ops:
                # If predecessor operation is not the main operation or in the merged_gaussian_ops list, remove it.
                # It will be merged later. This ensures order of operations is enforced
                if pre_op is not op and pre_op not in merged_gaussian_ops:
                    pre_op_qumodes = self.get_qumodes_operated_upon(pre_op)
                    if any(reg in pre_op_qumodes for reg in op_qumodes):
                        remove_op = True

            if remove_op:
                new_merged_gaussian_ops.remove(gaussian_op)
        return new_merged_gaussian_ops

    def organize_merge_ops(self, merged_gaussian_ops):
        organized_merge_operations = []
        for op_in_seq in self.curr_seq:
            if op_in_seq in merged_gaussian_ops:
                organized_merge_operations.append(op_in_seq)
        return organized_merge_operations

