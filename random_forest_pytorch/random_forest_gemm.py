import numpy as np
import torch
from utils import compute_path

class DecisionTreeGemm:
    """Implementation proposed from `Taming Model Serving Complexity, Performance
    and Cost: A Compilation to Tensor Computations Approach`
    Args:
        decision_tree (sklearn DecisionTree)
    """
    
    def __init__(self, decision_tree, backend="numpy", device="cpu"):
        """Create matrix A,B,C,D,E from decision_tree"""
        assert backend in ["numpy", "torch"]
        self.backend = backend
        self.back = np if backend == "numpy" else torch

        assert device in ["cpu", "cuda"]
        self.device = device

        tree = decision_tree.tree_

        is_internal_nodes = tree.children_left != tree.children_right
        internal_nodes = np.flatnonzero(is_internal_nodes)
        leaf_nodes = np.flatnonzero(~is_internal_nodes)
        split_features_internal_nodes = tree.feature[internal_nodes]
        
        # Matrix A
        self.A = np.zeros(shape=(tree.n_features, len(internal_nodes)))
        for i,j in enumerate(split_features_internal_nodes):
            self.A[j,i] = 1
        
        # Matrix B
        self.B = tree.threshold[internal_nodes]
        
        # Matrix C
        sub_to_global_internal_nodes = { v:k for k,v in enumerate(internal_nodes) }
        s_to_g = lambda x : sub_to_global_internal_nodes[x]
        self.C = np.zeros(shape=(len(internal_nodes), len(leaf_nodes)))
        
        for j, leaf_idx in enumerate(leaf_nodes):
            path = compute_path(tree, leaf_idx)

            for i in range(len(path)):
                # Apply transformation on index
                # subset (internal nodes)
                #      -> global (internal and leaf nodes)
                path[i][0] = s_to_g(path[i][0])

            for node_idx, value in path:
                self.C[node_idx,j] = value
        
        # Matrix D
        self.D = np.sum(self.C == 1, axis=0)
        
        # Matrix E
        self.E = np.zeros(shape=(len(leaf_nodes), decision_tree.n_classes_))
        leaf_to_class = tree.value[leaf_nodes].argmax(axis=-1).flatten()

        for i in range(len(leaf_nodes)):
            self.E[i,leaf_to_class[i]] = 1
        
        if backend == "torch":
            self.A, self.B, self.C, self.D, self.E = map(
                lambda x: torch.tensor(x, device=self.device, dtype=torch.float32),
                (self.A, self.B, self.C, self.D, self.E)
            )
    
    def convert_to_float(self, tensor):
        if self.backend == "numpy":
            return tensor.astype(np.float32)
        else:
            return tensor.float()

    def _GEMM(self, X):
        """Implement GEMM Strategy"""
        T = X @ self.A
        T = self.convert_to_float(T < self.B)
        T = T @ self.C
        T = self.convert_to_float(T == self.D)
        T = T @ self.E
        return T

    def predict(self, X):
        """Return class (integer) for each data point"""
        T = self._GEMM(X)
        return self.back.argmax(T, axis=1)
    
    def predict_onehot(self, X):
        """One Hot Encoding version of self.predict"""
        return self._GEMM(X)

class RandomForestGEMM:
    def __init__(self, random_forest, backend="numpy", device="cpu"):
        """Create estimators from random_forest"""
        assert backend in ["numpy", "torch"]
        self.backend = backend
        self.back = np if backend == "numpy" else torch
        self.device = device

        self.trees = [DecisionTreeGemm(estimator, backend, device) for estimator in random_forest.estimators_]
        self.n_classes_ = random_forest.n_classes_
    
    def vote(self, X):
        """Count the vote from each tree for each data point"""
        #votes = self.back.empty(len(self.trees), X.shape[0], self.n_classes_)
        #if self.backend == "torch":
        #    votes = votes.to(self.device)
        #for i, e in enumerate(self.trees):
        #    votes[i, :, :] = e.predict_onehot(X)
        #return votes.sum(axis=0)
        return self.back.stack([e.predict_onehot(X) for e in self.trees]).sum(axis=0)

    def predict(self, X):
        predictions = self.vote(X)
        return self.back.argmax(predictions, axis=1)
