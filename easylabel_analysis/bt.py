"""Within-scene Davidson / Bradley–Terry fitting, ported unchanged in substance from legacy-v0
(annotation_mvp/scaling.py). No implicit penalty: unidentifiable data returns ok=False, never
fabricated scores or standard errors.

Rows are dicts {'a': name, 'b': name, 'outcome': 'a' | 'b' | 'tie'}."""
import math

import networkx as nx
import numpy as np
from scipy.linalg import helmert
from scipy.optimize import minimize
from scipy.special import logsumexp, softmax


def wilson(success, total, z=1.959963984540054):
    success, total = int(success), int(total)
    if not total:
        return dict(n=0, success=success, rate=None, ci=None)
    p = success / total
    d = 1 + z * z / total
    center = (p + z * z / (2 * total)) / d
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / d
    return dict(n=total, success=success, rate=p, ci=[center - half, center + half])


def observed_graph(names, rows, tie_arcs=False):
    """Directed win graph (winner → loser); ties optionally add arcs both ways."""
    g = nx.DiGraph()
    g.add_nodes_from(names)
    for r in rows:
        a, b = r['a'], r['b']
        if a not in g or b not in g:
            continue
        if r['outcome'] == 'tie':
            if tie_arcs:
                g.add_edge(a, b)
                g.add_edge(b, a)
        elif r['outcome'] == 'a':
            g.add_edge(a, b)
        else:
            g.add_edge(b, a)
    return g


def connectivity(names, rows):
    strict = observed_graph(names, rows)
    ties = observed_graph(names, rows, True)
    comps = list(nx.strongly_connected_components(strict))
    return dict(strict_strong=bool(names) and nx.is_strongly_connected(strict),
                tie_augmented_strong=bool(names) and nx.is_strongly_connected(ties),
                components=[sorted(c) for c in comps],
                note='胜负图不含平局边；另报把平局当双向支持的图。Davidson 仍须检查参数边界与 Hessian。')


def fit(names, rows, half_ties=False):
    """Davidson model (ties via ν) or BT with ties counted as half wins."""
    names = list(names)
    if len(names) < 2:
        return dict(ok=False, reason='少于两张图片')
    idx = {n: k for k, n in enumerate(names)}
    valid = [r for r in rows if r['a'] in idx and r['b'] in idx]
    graph = observed_graph(names, valid, tie_arcs=True)
    if not nx.is_strongly_connected(graph):
        return dict(ok=False, reason='响应支持图不强连通；不存在有限的全局无惩罚估计')
    left = np.array([idx[r['a']] for r in valid])
    right = np.array([idx[r['b']] for r in valid])
    outcomes = np.array([0 if r['outcome'] == 'a' else 1 if r['outcome'] == 'b' else 2 for r in valid])
    n = len(names)
    basis = helmert(n, full=False).T
    has_ties = bool(np.any(outcomes == 2)) and not half_ties
    if np.all(outcomes == 2) and not half_ties:
        return dict(ok=False, reason='全部为平局；平局参数发散，不能给出有限联合MLE')
    design = (basis[left] - basis[right]) / 2
    dimensions = n - 1 + int(has_ties)

    def objective(theta):
        delta = np.einsum('ij,j->i', design, theta[:n - 1])
        if has_ties:
            logits = np.column_stack([delta, -delta, np.full(len(delta), theta[-1])])
            target = np.eye(3)[outcomes]
        else:
            logits = np.column_stack([delta, -delta])
            target = np.column_stack([np.where(outcomes == 0, 1, np.where(outcomes == 2, .5, 0)),
                                      np.where(outcomes == 1, 1, np.where(outcomes == 2, .5, 0))])
        p = softmax(logits, axis=1)
        loss = float(np.sum(logsumexp(logits, axis=1) - np.sum(target * logits, axis=1)))
        grad_s = np.einsum('ij,i->j', design, (p[:, 0] - target[:, 0]) - (p[:, 1] - target[:, 1]))
        grad = np.r_[grad_s, np.sum(p[:, 2] - target[:, 2])] if has_ties else grad_s
        return loss, grad

    result = minimize(objective, np.zeros(dimensions), jac=True, method='BFGS', options={'gtol': 1e-7, 'maxiter': 2000})
    loss, gradient = objective(result.x)
    if not np.isfinite(loss) or np.linalg.norm(gradient, np.inf) > 2e-4 or np.max(np.abs(result.x)) > 35:
        return dict(ok=False, reason='无惩罚优化未收敛或参数趋于边界', optimizer_message=str(result.message))
    # Central differences of the analytic gradient, jointly including log(nu).
    hessian = np.zeros((dimensions, dimensions))
    for j in range(dimensions):
        step = 1e-4 * max(1, abs(result.x[j]))
        dx = np.zeros(dimensions)
        dx[j] = step
        hessian[:, j] = (objective(result.x + dx)[1] - objective(result.x - dx)[1]) / (2 * step)
    hessian = (hessian + hessian.T) / 2
    eigen = np.linalg.eigvalsh(hessian)
    if eigen[0] <= 1e-8 or eigen[-1] / eigen[0] > 1e12:
        return dict(ok=False, reason='约束空间Hessian非正定或病态；不输出虚假标准误')
    cov_theta = np.linalg.inv(hessian)
    covariance = np.einsum('ij,jk,lk->il', basis, cov_theta[:n - 1, :n - 1], basis, optimize=False)
    scores = np.einsum('ij,j->i', basis, result.x[:n - 1])
    if not np.all(np.isfinite(covariance)) or not np.all(np.isfinite(scores)):
        return dict(ok=False, reason='非有限数值结果，停止发布标度')
    return dict(ok=True, names=names, scores=scores, covariance=covariance,
                se=np.sqrt(np.maximum(0, np.diag(covariance))),
                nu=float(np.exp(result.x[-1])) if has_ties else 0., nll=loss,
                tie_parameter_boundary=not has_ties and not half_ties, n_observations=len(valid))


def prediction(model, a, b):
    """[P(a wins), P(b wins), P(tie)] under a fitted model."""
    index = {n: k for k, n in enumerate(model['names'])}
    delta = (model['scores'][index[a]] - model['scores'][index[b]]) / 2
    if model['nu'] == 0:
        return np.r_[softmax([delta, -delta]), 0.]
    return softmax([delta, -delta, math.log(model['nu'])])


def pair_information(model, a, b):
    """Expected Fisher information about the score difference from one more judgement, under the
    half-tie BT approximation: p(1-p) of the decisive outcome. Used by active sampling."""
    p_a, p_b, _ = prediction(model, a, b)
    decisive = p_a + p_b
    if decisive <= 0:
        return 0.0
    p = p_a / decisive
    return float(p * (1 - p))
