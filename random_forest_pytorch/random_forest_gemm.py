import torch
import numpy as np

from utils import compute_path

class DecisionTreeGemm:
    """Implementation proposed from `Taming Model Serving Complexity, Performance
    and Cost: A Compilation to Tensor Computations Approach`
    Args:
        decision_tree (sklearn DecisionTree)
    """
    
    def __init__(self, decision_tree):
        """Create matrix A,B,C,D,E from decision_tree"""
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
        
        self.A = torch.from_numpy(self.A)
        self.B = torch.from_numpy(self.B)
        self.C = torch.from_numpy(self.C)
        self.D = torch.from_numpy(self.D)
        self.E = torch.from_numpy(self.E)
        
        if torch.cuda.is_available():
            self.A = self.A.to('cuda')
            self.B = self.B.to('cuda')
            self.C = self.C.to('cuda')
            self.D = self.D.to('cuda')
            self.E = self.E.to('cuda')
    
    def _GEMM(self, X):
        """Implement GEMM Strategy"""
        T = X.matmul(self.A)
        T = (T < self.B).type(torch.float64)
        T = T.matmul(self.C)
        T = (T == self.D).type(torch.float64)
        T = T.matmul(self.E)
        return T

    def predict(self, X):
        """Return class (integer) for each data point"""
        T = self._GEMM(X)
        return torch.argmax(T,dim=1)
    
    def predict_onehot(self, X):
        """One Hot Encoding version of self.predict"""
        return self._GEMM(X)

class RandomForestGEMM:
    
    def __init__(self, random_forest):
        """Create estimators from random_forest"""
        self.trees = [DecisionTreeGemm(estimator) for estimator in random_forest.estimators_]
        self.n_classes_ = random_forest.n_classes_
    
    def vote(self, X):
        """Count the vote from each tree for each data point"""
        return torch.stack([e.predict_onehot(X) for e in self.trees], dim=0).sum(dim=0)

    def predict(self, X):
        predictions = self.vote(X)
        return torch.argmax(predictions, dim=1)