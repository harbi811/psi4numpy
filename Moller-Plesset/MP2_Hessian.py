# -*- coding: utf-8 -*-
"""
This script calculates nuclear hessians for MP2 using the
gradients of one and two electron integrals obtained from PSI4. 

References: 
1. "Derivative studies in hartree-fock and møller-plesset theories",
J. A. Pople, R. Krishnan, H. B. Schlegel and J. S. Binkley
DOI: 10.1002/qua.560160825

2. "Analytic evaluation of second derivatives using second-order many-body 
perturbation theory and unrestricted Hartree-Fock reference functions",
J. F. Stanton, J. Gauss, and R. J. Bartlett
DOI: 10.1016/0009-2614(92)86135-5

3. "Coupled-cluster open shell analytic gradients: Implementation of the
direct product decomposition approach in energy gradient calculations",
J. Gauss, J. F. Stanton, R. J. Bartlett
DOI: 10.1063/1.460915
"""

__authors__ = "Kirk C. Pearce"
__credits__ = ["Kirk C. Pearce", "Ashutosh Kumar"]
__copyright__ = "(c) 2014-2017, The Psi4NumPy Developers"
__license__ = "BSD-3-Clause"
__date__ = "2019-02-11"

import time
import numpy as np
#import psi4
import copy

import psi4
from psi4 import *
from psi4.core import *
from psi4.driver.diatomic import anharmonicity
from psi4.driver.gaussian_n import *
from psi4.driver.frac import ip_fitting, frac_traverse
from psi4.driver.aliases import *
from psi4.driver.driver_cbs import *
from psi4.driver.wrapper_database import database, db, DB_RGT, DB_RXN
from psi4.driver.wrapper_autofrag import auto_fragments
from psi4.driver.constants.physconst import *
psi4_io = core.IOManager.shared_object()


# Setup NumPy options
np.set_printoptions(
    precision=15, 
    linewidth=200, 
    suppress=True, 
    threshold=np.nan
)

psi4.set_memory(int(1e9), False)
#psi4.core.set_output_file('output.dat', False)
psi4.core.set_num_threads(4)

# Specify Molecule
#mol = psi4.geometry("""
#O
#H 1 R
#H 1 R 2 104
#symmetry c1
#""")
mol = psi4.geometry("""
O   0.00000000     0.00000000     0.07579184
H   0.00000000    -0.86681183    -0.60143578
H   0.00000000     0.86681183    -0.60143578
symmetry c1
units bohr
noreorient
""")

# physical constants changed, so geometry changes slightly
from pkg_resources import parse_version
mol.R = 1.1
#if parse_version(psi4.__version__) >= parse_version("1.3a1"):
#    mol.R = 1.1 * 0.52917721067 / 0.52917720859
#else:
#    mol.R = 1.1

psi4.core.set_active_molecule(mol)

# Set Psi4 Options
options = {
    #'BASIS': 'STO-3G',
    'SCF_TYPE': 'PK',
    'GUESS': 'CORE',
    'MP2_TYPE': 'CONV',
    'E_CONVERGENCE': 1e-14,
    'D_CONVERGENCE': 1e-14,
    'print': 1
}

# Define custom basis set
def basisspec_psi4_yo__anonymous203a4092(mol, role):
    basstrings = {}
    mol.set_basis_all_atoms("sto-3g", role=role)
    basstrings['sto-3g'] = """
cartesian
****
H     0
S   3   1.00
3.4252509             0.1543290
0.6239137             0.5353281
0.1688554             0.4446345
****
O     0
S   3   1.00
130.7093214              0.1543290
23.8088661              0.5353281
6.4436083              0.4446345
SP   3   1.00
5.0331513             -0.0999672             0.1559163
1.1695961              0.3995128             0.6076837
0.3803890              0.7001155             0.3919574
****
"""
    return basstrings
qcdb.libmintsbasisset.basishorde['ANONYMOUS203A4092'] = basisspec_psi4_yo__anonymous203a4092
core.set_global_option("BASIS", "anonymous203a4092")

psi4.set_options(options)

# Perform MP2 Energy Calculation
rhf_e, wfn = psi4.energy('SCF', return_wfn=True)
#mp2_e, wfn = psi4.energy('MP2', return_wfn=True)

# Relevant Variables
natoms = mol.natom()
nmo = wfn.nmo()
nocc = wfn.doccpi()[0]
nvir = nmo - nocc

# MO Coefficients
C = wfn.Ca_subset("AO", "ALL")
npC = psi4.core.Matrix.to_array(C)

# Integral generation from Psi4's MintsHelper
mints = psi4.core.MintsHelper(wfn.basisset())

# Build T, V, and S
T = mints.ao_kinetic()
npT = psi4.core.Matrix.to_array(T)
V = mints.ao_potential()
npV = psi4.core.Matrix.to_array(V)
S = mints.ao_overlap()
npS = psi4.core.Matrix.to_array(S)

# Transform S to MO Basis
npS_mo = 2.0 * np.einsum('uj,vi,uv', npC, npC, npS, optimize=True)
print(npS_mo)

# Build ERIs
ERI = mints.mo_eri(C, C, C, C)
npERI = psi4.core.Matrix.to_array(ERI)
# Physicist notation
npERI = npERI.swapaxes(1, 2)

# Build Core Hamiltonian in AO basis
H_core = npT + npV

# Transform H to MO basis
H = np.einsum('uj,vi,uv', npC, npC, H_core, optimize=True)

# Build Fock Matrix
F = H + 2.0 * np.einsum('pmqm->pq', npERI[:, :nocc, :, :nocc], optimize=True)
F -= np.einsum('pmmq->pq', npERI[:, :nocc, :nocc, :], optimize=True)

# Occupied and Virtual Orbital Energies
F_occ = np.diag(F)[:nocc]
F_vir = np.diag(F)[nocc:nmo]

# Build Denominator
Dijab = F_occ.reshape(-1, 1, 1, 1) + F_occ.reshape(-1, 1, 1) - F_vir.reshape(-1, 1) - F_vir

# Build T2 Amplitudes,
# where t2 = <ij|ab> / (e_i + e_j - e_a - e_b),
t2 = npERI[:nocc, :nocc, nocc:, nocc:] / Dijab

# Build T2_tilde Amplitudes (tilde = closed-shell spin-free analog of antisymmetrizer),
# i.e., t2_tilde[p,q,r,s] = 2 * t2[p,q,r,s] - t2[p,q,s,r]),
# where t2_tilde = [2<ij|ab> - <ij|ba>] / (e_i + e_j - e_a - e_b)
t2_tilde = 2 * t2 - t2.swapaxes(2, 3)

# Build Reference OPDM
npD_ao = 2.0 * np.einsum('ui,vi->uv', npC[:, :nocc], npC[:, :nocc], optimize=True)
# Transform to MO Basis
ref_opdm = np.einsum('iu,uv,vw,wx,xj', npC.T, npS.T, npD_ao, npS, npC, optimize=True)

# Build MP2 OPDM
# Build OO block of MP2 OPDM
# Pij = sum_kab [( t2(i,k,a,b) * t2_tilde(j,k,a,b) ) + ( t2(j,k,a,b) * t2_tilde(i,k,a,b) )]
Pij = -1.0 * np.einsum('ikab,jkab->ij', t2, t2_tilde, optimize=True)
Pij += -1.0 * np.einsum('jkab,ikab->ij', t2, t2_tilde, optimize=True)

# Build VV block of MP2 OPDM
# Pab = sum_ijc [( t2(i,j,a,c) * t2_tilde(i,j,b,c) ) + ( t2(i,j,b,c) * t2_tilde(i,j,a,c) )]
Pab = np.einsum('ijac,ijbc->ab', t2, t2_tilde, optimize=True)
Pab += np.einsum('ijbc,ijac->ab', t2, t2_tilde, optimize=True)

# Build Total OPDM
Ppq = np.zeros((nmo, nmo))
Ppq += ref_opdm
Ppq[:nocc, :nocc] += Pij
Ppq[nocc:, nocc:] += Pab
print("\n\nTotal OPDM:\n", Ppq)
print("\nChecks:")
print("OPDM is symmetric: ",np.allclose(Ppq, Ppq.T))
print("OPDM trace = 10: ",np.isclose(sum(np.linalg.eigh(Ppq)[0]),10))

# Build Reference TPDM
ref_tpdm = np.zeros((nmo, nmo, nmo, nmo))
ref_tpdm += 2.0 * np.einsum("pr,qs->pqrs", ref_opdm, ref_opdm, optimize=True)
ref_tpdm -= 1.0 * np.einsum("ps,qr->pqrs", ref_opdm, ref_opdm, optimize=True)
ref_tpdm = -0.25 * ref_tpdm

# Build MP2 TPDM
Pijab = copy.deepcopy(t2_tilde)

# Build Total TPDM
Ppqrs = np.zeros((nmo, nmo, nmo, nmo))
Ppqrs += ref_tpdm
Ppqrs[:nocc, :nocc, nocc:, nocc:] += Pijab
Ppqrs[nocc:, nocc:, :nocc, :nocc] += Pijab.T
#print("\n\nTotal TPDM:\n", Ppqrs.reshape(nmo*nmo, nmo*nmo))


# Build dDpq/da:
# For SCF, dDpq/da = 0
#dPpq = {}

# Build dPpqrs/da:
# For SCF, dPpqrs/da = 0
#dPpqrs = {}


# Build I'
# I'_pq = - (1/2) * [ fpp(Ppq + Pqp) + sum_rs (Prs * (4<rp|sq> - <rp|qs> - <rq|ps>)) * kronecker_delta(q,occ) + ...
#         ... + sum_rst (Pqrst <pr|st> + Prqst <rp|st> + Prsqt <rs|pt> + Prstq <rs|tp>) ]
Ip = np.zeros((nmo, nmo))

# I'pq += fpp(Ppq + Pqp)
Ip += np.einsum("pr,rq->pq", F, Ppq, optimize=True)
Ip += np.einsum("qr,rp->pq", Ppq, F, optimize=True)

# I'_pq += sum_rst (Pqrst <pr|st> + Prqst <rp|st> + Prsqt <rs|pt> + Prstq <rs|tp>)
Ip += np.einsum('qrst,prst->pq', Ppqrs, npERI, optimize=True)
Ip += np.einsum('rqst,rpst->pq', Ppqrs, npERI, optimize=True)
Ip += np.einsum('rsqt,rspt->pq', Ppqrs, npERI, optimize=True)
Ip += np.einsum('rstq,rstp->pq', Ppqrs, npERI, optimize=True)

# I'_pq += sum_rs Prs(4<rp|sq> - <rp|qs> - <rq|ps>) kronecker_delta(q,occ)
Ip[:, :nocc] += 4.0 * np.einsum('rs,rpsq->pq', Ppq , npERI[:, :, :, :nocc], optimize=True)
Ip[:, :nocc] -= 1.0 * np.einsum('rs,rpqs->pq', Ppq , npERI[:, :, :nocc, :], optimize=True)
Ip[:, :nocc] -= 1.0 * np.einsum('rs,rqps->pq', Ppq , npERI[:, :nocc, :, :], optimize=True)

Ip *= -0.5
print("\nI':\n",Ip)


# Build I'' ,
# where I''_pq = I'_qp    if (p,q) = (a,i)
#              = I'_pq    otherwise
Ipp = copy.deepcopy(Ip)
Ipp[nocc:, :nocc] = Ip[:nocc, nocc:].T

# Build X_ai = I'_ia - I'_ai
X = Ip[:nocc, nocc:].T - Ip[nocc:, :nocc]
print("\nX:\n", X)

# Build Idenity matrices in nocc/nvir dimensions
I_occ = np.diag(np.ones(nocc))
I_vir = np.diag(np.ones(nvir))

# Build epsilon_a - epsilon_i matrix
eps = np.asarray(wfn.epsilon_a())
eps_diag = eps[nocc:].reshape(-1, 1) - eps[:nocc]

# Build the electronic hessian, G, where
# G = ((epsilon_a - epsilon_i) * kronecker_delta(a,b) * kronecker_delta(i,j)) * (4<ij|ab> - <ij|ba> - <ia|jb>)

# G += 4<ij|ab> - <ij|ba> - <ia|jb>
G =  4.0 * npERI[:nocc, :nocc, nocc:, nocc:]
G -= 1.0 * npERI[:nocc, :nocc, nocc:, nocc:].swapaxes(2, 3)
G -= 1.0 * npERI[:nocc, nocc:, :nocc, nocc:].swapaxes(1, 2)

# Change shape of G from ij,ab to ia,jb
G = G.swapaxes(1, 2)

# G += (epsilon_a - epsilon_i) * kronecker_delta(a,b) * kronecker delta(i,j)
G += np.einsum('ai,ij,ab->iajb', eps_diag, I_occ, I_vir, optimize=True)

# Inverse of G
Ginv = np.linalg.inv(G.reshape(nocc * nvir, -1))
Ginv = Ginv.reshape(nocc, nvir, nocc, nvir)

# Take Transpose of G_iajb
G = G.T.reshape(nocc * nvir, nocc * nvir)
print("\nMO Hessian, G:\n", G)

# Solve G^T(ai,bj) Z(b,j) = X(a,i)
X = X.reshape(nocc * nvir, -1)
Z = np.linalg.solve(G, X).reshape(nvir, -1)
print("\nZ Vector:\n", X)

# Relax OPDM
# Ppq(a,i) = Ppq(i,a) = - Z(a,i)
Ppq[:nocc, nocc:] = -Z.T
Ppq[nocc:, :nocc] = -Z
print("\n\nRelaxed Total OPDM:\n", Ppq)

# Build Lagrangian, I, where
# I(i,j) = I''(i,j) + sum_ak ( Z(a,k) * [ 2<ai|kj> - <ai|jk>  + 2<aj|ki> - <aj|ik> ])
# I(i,a) = I''(i,a) + Z(a,i) * eps(i)
# I(a,i) = I''(a,i) + Z(a,i) * eps(i)
# I(a,b) = I''(a,b)
I = copy.deepcopy(Ipp)

# I(i,j) 
I[:nocc, :nocc] += 2.0 * np.einsum('ak,aikj->ij', Z, npERI[nocc:, :nocc, :nocc, :nocc], optimize=True)
I[:nocc, :nocc] -= 1.0 * np.einsum('ak,aijk->ij', Z, npERI[nocc:, :nocc, :nocc, :nocc], optimize=True)
I[:nocc, :nocc] += 2.0 * np.einsum('ak,ajki->ij', Z, npERI[nocc:, :nocc, :nocc, :nocc], optimize=True)
I[:nocc, :nocc] -= 1.0 * np.einsum('ak,ajik->ij', Z, npERI[nocc:, :nocc, :nocc, :nocc], optimize=True)

# I(a,i)
I[nocc:, :nocc] += Z * F_occ

# I(i,a)
I[:nocc, nocc:] += (Z * F_occ).T

print("\n\nLagrangian I:\n", I)



# Build Integral Derivatives
cart = ['_X', '_Y', '_Z']
oei_dict = {"S": "OVERLAP", "T": "KINETIC", "V": "POTENTIAL"}

deriv1_mat = {}
deriv1_np = {}

# 1st Derivative of OEIs
for atom in range(natoms):
    for key in oei_dict:
        deriv1_mat[key + str(atom)] = mints.mo_oei_deriv1(oei_dict[key], atom, C, C)
        for p in range(3):
            map_key = key + str(atom) + cart[p]
            deriv1_np[map_key] = np.asarray(deriv1_mat[key + str(atom)][p])

# 1st Derivative of TEIs
for atom in range(natoms):
    key = "TEI"
    deriv1_mat[key + str(atom)] = mints.mo_tei_deriv1(atom, C, C, C, C)
    for p in range(3):
        map_key = key + str(atom) + cart[p]
        deriv1_np[map_key] = np.asarray(deriv1_mat[key + str(atom)][p])

Hes = {};
deriv2_mat = {}
deriv2 = {}

Hes["N"] = np.zeros((3 * natoms, 3 * natoms))
Hes["S"] = np.zeros((3 * natoms, 3 * natoms))
Hes["T"] = np.zeros((3 * natoms, 3 * natoms))
Hes["V"] = np.zeros((3 * natoms, 3 * natoms))
Hes["J"] = np.zeros((3 * natoms, 3 * natoms))
Hes["K"] = np.zeros((3 * natoms, 3 * natoms))
Hes["TEI"] = np.zeros((3 * natoms, 3 * natoms))
Hes["R"] = np.zeros((3 * natoms, 3 * natoms))
Hessian = np.zeros((3 * natoms, 3 * natoms))

# 2nd Derivative of Nuclear Repulsion
Hes["N"] = np.asarray(mol.nuclear_repulsion_energy_deriv2())

psi4.core.print_out("\n\n")
Mat = psi4.core.Matrix.from_array(Hes["N"])
Mat.name = "NUCLEAR HESSIAN"
Mat.print_out()

# 2nd Derivative of OEIs
for atom1 in range(natoms):
    for atom2 in range(atom1 + 1):
        for key in  oei_dict:
            string = key + str(atom1) + str(atom2)
            deriv2_mat[string] = mints.mo_oei_deriv2(oei_dict[key], atom1, atom2, C, C)
            pq = 0
            for p in range(3):
                for q in range(3):
                    map_key = string + cart[p] + cart[q]
                    deriv2[map_key] = np.asarray(deriv2_mat[string][pq])
                    pq += 1
                    row = 3 * atom1 + p
                    col = 3 * atom2 + q
                    if key == "S":
                        Hes[key][row][col] = np.einsum("pq,pq->", I, deriv2[map_key], optimize=True)
                    else:
                        Hes[key][row][col] = np.einsum("pq,pq->", Ppq, deriv2[map_key], optimize=True)
                    Hes[key][col][row] = Hes[key][row][col]
                    Hes[key][col][row] = Hes[key][row][col]

for key in Hes:
    Mat = psi4.core.Matrix.from_array(Hes[key])
    if key in oei_dict:
        Mat.name = oei_dict[key] + " HESSIAN"
        Mat.print_out()
        psi4.core.print_out("\n")


# 2nd Derivative of TEIs
for atom1 in range(natoms):
    for atom2 in range(atom1 + 1):
        string = "TEI" + str(atom1) + str(atom2)
        deriv2_mat[string] = mints.mo_tei_deriv2(atom1, atom2, C, C, C, C)
        pq = 0
        for p in range(3):
            for q in range(3):
                map_key = string + cart[p] + cart[q]
                deriv2[map_key] = np.asarray(deriv2_mat[string][pq])
                pq = pq + 1
                row = 3 * atom1 + p
                col = 3 * atom2 + q

                Hes["TEI"][row][col] =  2.0 * np.einsum("pq,pqmm->", Ppq, deriv2[map_key][:, :, :nocc, :nocc], optimize=True)
                Hes["TEI"][row][col] -= 1.0 * np.einsum("pq,pmmq->", Ppq, deriv2[map_key][:, :nocc, :nocc, :], optimize=True)

                Hes["TEI"][row][col] += np.einsum("pqrs,prqs->", Ppqrs, deriv2[map_key], optimize=True)

                Hes["TEI"][col][row] = Hes["TEI"][row][col]

TEIMat = psi4.core.Matrix.from_array(Hes["TEI"])
TEIMat.name = " TEI HESSIAN"
TEIMat.print_out()

# Solve the CPHF equations here,  G_aibj Ubj^x = Bai^x (Einstein summation),
# where G is the electronic hessian,
# G_aibj = delta_ij * delta_ab * epsilon_ij * epsilon_ab + 4 <ij|ab> - <ij|ba> - <ia|jb>,
# where epsilon_ij = epsilon_i - epsilon_j, (epsilon -> orbital energies),
# x refers to the perturbation, Ubj^x are the corresponsing CPHF coefficients
# and Bai^x = Sai^x * epsilon_ii - Fai^x + Smn^x  * (2<am|in> - <am|ni>),
# where, S^x =  del(S)/del(x), F^x =  del(F)/del(x).

B = {}
F_grad = {}
U = {}

# Build Fpq^x
for atom in range(natoms):
    for p in range(3):
        key = str(atom) + cart[p]
        F_grad[key] = copy.deepcopy(deriv1_np["T" + key])
        F_grad[key] += deriv1_np["V" + key]
        F_grad[key] += 2.0 * np.einsum('pqmm->pq', deriv1_np["TEI" + key][:, :, :nocc, :nocc])
        F_grad[key] -= 1.0 * np.einsum('pmmq->pq', deriv1_np["TEI" + key][:, :nocc, :nocc, :])


psi4.core.print_out("\n\n CPHF Coefficentsn:\n")

# Build Bai^x
for atom in range(natoms):
    for p in range(3):
        key = str(atom) + cart[p]
        B[key] =  np.einsum("ai,ii->ai", deriv1_np["S" + key][nocc:, :nocc], F[:nocc, :nocc])
        B[key] -= F_grad[key][nocc:, :nocc]
        B[key] +=  2.0 * np.einsum("amin,mn->ai", npERI[nocc:, :nocc, :nocc, :nocc], deriv1_np["S" + key][:nocc, :nocc])
        B[key] += -1.0 * np.einsum("amni,mn->ai", npERI[nocc:, :nocc, :nocc, :nocc], deriv1_np["S" + key][:nocc, :nocc])

        # Compute U^x, where
        # U_ij^x = - 1/2 S_ij^a
        # U_ai^x = G^(-1)_aibj * B_bj^x
        # U_ia^x = - (U_ai^x + S_ai^x)
        # U_ab^x = - 1/2 S_ab^a
        U[key] = np.zeros((nmo, nmo))
        U[key][:nocc, :nocc] = - 0.5 * deriv1_np["S" + key][:nocc, :nocc]
        U[key][nocc:, :nocc] = np.einsum("iajb,bj->ai", Ginv, B[key])
        U[key][:nocc, nocc:] = - (U[key][nocc:, :nocc] + deriv1_np["S" + key][nocc:, :nocc]).T
        U[key][nocc:, nocc:] = - 0.5 * deriv1_np["S" + key][nocc:, nocc:]
        psi4.core.print_out("\n")
        UMat = psi4.core.Matrix.from_array(U[key])
        UMat.name = key
        UMat.print_out()

#dPpq = {}
#for atom in range(natoms):
#    for p in range(3):
#        key = str(atom) + cart[p]
#
#        # SCF Derivatives of OPDM
#        dPpq[key] =  1.0 * np.einsum('tp,tq->pq', U[key], Ppq)
#        dPpq[key] += 1.0 * np.einsum('tq,pt->pq', U[key], Ppq)
#        print("\nCurrent dPpq/da[", key, "]:\n", dPpq[key])
#
#        dPpq[key] = 2.0 * np.einsum('tm,pt,mq->pq', U[key][:, :nocc], npS_mo[:, :], npS_mo[:nocc, :])
#        dPpq[key] += 2.0 * np.einsum('tm,pm,tq->pq', U[key][:, :nocc], npS_mo[:, :nocc], npS_mo[:, :])
#        print("\nCurrent dPpq/da[", key, "]:\n", dPpq[key])
#
#        dPpq[key] = 1.0 * np.einsum('pm,mq->pq', deriv1_np["S" + key][:, :nocc], npS_mo[:nocc, :])
#        dPpq[key] += 1.0 * np.einsum('pm,mq->pq', npS_mo[:, :nocc], deriv1_np["S" + key][:nocc, :])
#        print("\nCurrent dPpq/da[", key, "]:\n", dPpq[key])


# MP2 Derivaties of OPDM
#
# Derivative of t2 amplitudes
for atom in range(natoms):
    for p in range(3):
        string = "T2" 
        key = str(atom) + cart[p]
        map_key = string + key

        # d{t2_ab^ij}/dx = <ij|ab>^x + sum_t [ ( Uti^x * <tj|ab> ) + ( Utj^x * <it|ab> ) + ( Uta^x * <ij|tb> ) + ( Utb^x * <ij|at> ) ] + ...
        #                  ... + t_cb^ij * dF_ac/dx + t_ac^ij * dF_bc/dx + t_ab^kj * dF_ki/dx + t_ab^ik * dF_kj/dx

        # d{t2_ab^ij}/dx += <ij|ab>^x + sum_t [ ( Uti^x * <tj|ab> ) + ( Utj^x * <it|ab> ) + ( Uta^x * <ij|tb> ) + ( Utb^x * <ij|at> ) ]
        deriv1_np[map_key] = copy.deepcopy(deriv1_np["TEI" + key][:nocc, nocc:, :nocc, nocc:].swapaxes(1,2))
        deriv1_np[map_key] += np.einsum('ti,tjab->ijab', U[key][:, :nocc], npERI[:, :nocc, nocc:, nocc:])
        deriv1_np[map_key] += np.einsum('tj,itab->ijab', U[key][:, :nocc], npERI[:nocc, :, nocc:, nocc:])
        deriv1_np[map_key] += np.einsum('ta,ijtb->ijab', U[key][:, nocc:], npERI[:nocc, :nocc, :, nocc:])
        deriv1_np[map_key] += np.einsum('tb,ijat->ijab', U[key][:, nocc:], npERI[:nocc, :nocc, nocc:, :])

        # d{t2_ab^ij}/dx += t_cb^ij * dF_ac/dx
        deriv1_np[map_key] += np.einsum('ijcb,ac->ijab', t2, F_grad[key][nocc:, nocc:])
        deriv1_np[map_key] += np.einsum('ijcb,ta,tc->ijab', t2, U[key][:, nocc:], F[:, nocc:])
        deriv1_np[map_key] += np.einsum('ijcb,tc,at->ijab', t2, U[key][:, nocc:], F[nocc:, :]) 
        deriv1_np[map_key] -= 0.5 * 4.0 * np.einsum('ijcb,mn,amcn->ijab', t2, deriv1_np["S" + key][:nocc, :nocc], npERI[nocc:, :nocc, nocc:, :nocc])
        deriv1_np[map_key] += 0.5 * 1.0 * np.einsum('ijcb,mn,acmn->ijab', t2, deriv1_np["S" + key][:nocc, :nocc], npERI[nocc:, nocc:, :nocc, :nocc])
        deriv1_np[map_key] += 0.5 * 1.0 * np.einsum('ijcb,mn,acnm->ijab', t2, deriv1_np["S" + key][:nocc, :nocc], npERI[nocc:, nocc:, :nocc, :nocc])
        deriv1_np[map_key] += 1.0 * 4.0 * np.einsum('ijcb,dm,adcm->ijab', t2, U[key][nocc:, :nocc], npERI[nocc:, nocc:, nocc:, :nocc])
        deriv1_np[map_key] -= 1.0 * 1.0 * np.einsum('ijcb,dm,acdm->ijab', t2, U[key][nocc:, :nocc], npERI[nocc:, nocc:, nocc:, :nocc])
        deriv1_np[map_key] -= 1.0 * 1.0 * np.einsum('ijcb,dm,acmd->ijab', t2, U[key][nocc:, :nocc], npERI[nocc:, nocc:, :nocc, nocc:])
        #
        # d{t2_ab^ij}/dx += t_ac^ij * dF_bc/dx
        deriv1_np[map_key] += np.einsum('ijac,bc->ijab', t2, F_grad[key][nocc:, nocc:])
        deriv1_np[map_key] += np.einsum('ijac,tb,tc->ijab', t2, U[key][:, nocc:], F[:, nocc:])
        deriv1_np[map_key] += np.einsum('ijac,tc,bt->ijab', t2, U[key][:, nocc:], F[nocc:, :]) 
        deriv1_np[map_key] -= 0.5 * 4.0 * np.einsum('ijac,mn,bmcn->ijab', t2, deriv1_np["S" + key][:nocc, :nocc], npERI[nocc:, :nocc, nocc:, :nocc])
        deriv1_np[map_key] += 0.5 * 1.0 * np.einsum('ijac,mn,bcmn->ijab', t2, deriv1_np["S" + key][:nocc, :nocc], npERI[nocc:, nocc:, :nocc, :nocc])
        deriv1_np[map_key] += 0.5 * 1.0 * np.einsum('ijac,mn,bcnm->ijab', t2, deriv1_np["S" + key][:nocc, :nocc], npERI[nocc:, nocc:, :nocc, :nocc])
        deriv1_np[map_key] += 1.0 * 4.0 * np.einsum('ijac,dm,bdcm->ijab', t2, U[key][nocc:, :nocc], npERI[nocc:, nocc:, nocc:, :nocc])
        deriv1_np[map_key] -= 1.0 * 1.0 * np.einsum('ijac,dm,bcdm->ijab', t2, U[key][nocc:, :nocc], npERI[nocc:, nocc:, nocc:, :nocc])
        deriv1_np[map_key] -= 1.0 * 1.0 * np.einsum('ijac,dm,bcmd->ijab', t2, U[key][nocc:, :nocc], npERI[nocc:, nocc:, :nocc, nocc:])
        #
        # d{t2_ab^ij}/dx += t_ab^kj * dF_ki/dx
        deriv1_np[map_key] += -1.0 * np.einsum('kjab,ki->ijab', t2, F_grad[key][:nocc, :nocc])
        deriv1_np[map_key] += -1.0 * np.einsum('kjab,tk,ti->ijab', t2, U[key][:, :nocc], F[:, :nocc])
        deriv1_np[map_key] += -1.0 * np.einsum('kjab,ti,kt->ijab', t2, U[key][:, :nocc], F[:nocc, :]) 
        deriv1_np[map_key] -= -1.0 * 0.5 * 4.0 * np.einsum('kjab,mn,kmin->ijab', t2, deriv1_np["S" + key][:nocc, :nocc], npERI[:nocc, :nocc, :nocc, :nocc])
        deriv1_np[map_key] += -1.0 * 0.5 * 1.0 * np.einsum('kjab,mn,kimn->ijab', t2, deriv1_np["S" + key][:nocc, :nocc], npERI[:nocc, :nocc, :nocc, :nocc])
        deriv1_np[map_key] += -1.0 * 0.5 * 1.0 * np.einsum('kjab,mn,kinm->ijab', t2, deriv1_np["S" + key][:nocc, :nocc], npERI[:nocc, :nocc, :nocc, :nocc])
        deriv1_np[map_key] += -1.0 * 1.0 * 4.0 * np.einsum('kjab,dm,kdim->ijab', t2, U[key][nocc:, :nocc], npERI[:nocc, nocc:, :nocc, :nocc])
        deriv1_np[map_key] -= -1.0 * 1.0 * 1.0 * np.einsum('kjab,dm,kidm->ijab', t2, U[key][nocc:, :nocc], npERI[:nocc, :nocc, nocc:, :nocc])
        deriv1_np[map_key] -= -1.0 * 1.0 * 1.0 * np.einsum('kjab,dm,kimd->ijab', t2, U[key][nocc:, :nocc], npERI[:nocc, :nocc, :nocc, nocc:])
        #
        # d{t2_ab^ij}/dx += t_ab^ik * dF_kj/dx
        deriv1_np[map_key] += -1.0 * np.einsum('ikab,kj->ijab', t2, F_grad[key][:nocc, :nocc])
        deriv1_np[map_key] += -1.0 * np.einsum('ikab,tk,tj->ijab', t2, U[key][:, :nocc], F[:, :nocc])
        deriv1_np[map_key] += -1.0 * np.einsum('ikab,tj,kt->ijab', t2, U[key][:, :nocc], F[:nocc, :]) 
        deriv1_np[map_key] -= -1.0 * 0.5 * 4.0 * np.einsum('ikab,mn,kmjn->ijab', t2, deriv1_np["S" + key][:nocc, :nocc], npERI[:nocc, :nocc, :nocc, :nocc])
        deriv1_np[map_key] += -1.0 * 0.5 * 1.0 * np.einsum('ikab,mn,kjmn->ijab', t2, deriv1_np["S" + key][:nocc, :nocc], npERI[:nocc, :nocc, :nocc, :nocc])
        deriv1_np[map_key] += -1.0 * 0.5 * 1.0 * np.einsum('ikab,mn,kjnm->ijab', t2, deriv1_np["S" + key][:nocc, :nocc], npERI[:nocc, :nocc, :nocc, :nocc])
        deriv1_np[map_key] += -1.0 * 1.0 * 4.0 * np.einsum('ikab,dm,kdjm->ijab', t2, U[key][nocc:, :nocc], npERI[:nocc, nocc:, :nocc, :nocc])
        deriv1_np[map_key] -= -1.0 * 1.0 * 1.0 * np.einsum('ikab,dm,kjdm->ijab', t2, U[key][nocc:, :nocc], npERI[:nocc, :nocc, nocc:, :nocc])
        deriv1_np[map_key] -= -1.0 * 1.0 * 1.0 * np.einsum('ikab,dm,kjmd->ijab', t2, U[key][nocc:, :nocc], npERI[:nocc, :nocc, :nocc, nocc:])

        deriv1_np[map_key] /= Dijab


#dPpq = {}
#for atom in range(natoms):
#    for p in range(3):
#        key = str(atom) + cart[p]
#        # MP2 Derivatives of OPDM
#        dPpq[key] =  1.0 * np.einsum('tp,tq->pq', U[key], Ppq)
#        dPpq[key] += 1.0 * np.einsum('tq,pt->pq', U[key], Ppq)
#        print("\nCurrent dPpq/da[", key, "]:\n", dPpq[key])
#
#        dPpq[key] = 2.0 * np.einsum('tm,pt,mq->pq', U[key][:, :nocc], npS_mo[:, :], npS_mo[:nocc, :])
#        dPpq[key] += 2.0 * np.einsum('tm,pm,tq->pq', U[key][:, :nocc], npS_mo[:, :nocc], npS_mo[:, :])
#        print("\nCurrent dPpq/da[", key, "]:\n", dPpq[key])
#
#        dPpq[key] = 1.0 * np.einsum('pm,mq->pq', deriv1_np["S" + key][:, :nocc], npS_mo[:nocc, :])
#        dPpq[key] += 1.0 * np.einsum('pm,mq->pq', npS_mo[:, :nocc], deriv1_np["S" + key][:nocc, :])
#        print("\nCurrent dPpq/da[", key, "]:\n", dPpq[key])

#print(dPpq)



# Energy and Gradient Tests
#
# Energy Test
energy_test =  2.0 * np.einsum('ijab,ijab->', t2, npERI[:nocc, :nocc, nocc:, nocc:])
energy_test -= 1.0 * np.einsum('ijab,ijba->', t2, npERI[:nocc, :nocc, nocc:, nocc:])
print("Energy Test: ", energy_test)
#
# Gradient Test
scf_Gradient = [[ 0.0, -0.000000000000035, -2.430230565071191],
 [ 0.0,  1.678776347069892,  1.215115282535529],
 [-0.0, -1.678776347069857,  1.215115282535499]]
print("\nSCF Gradient:\n", np.asarray(scf_Gradient))

# Calculate SCF Gradient with Densities and U matrices to check Densities and U matrices
#testGradient = np.zeros((natoms, 3))
#Gradient = np.zeros((natoms, 3))
#for atom in range(natoms):
#    for p in range(3):
#        key = str(atom) + cart[p]
#
#        testGradient[atom, p] +=  1.0 * np.einsum('pq,pq->', Ppq, F_grad[key], optimize=True)
#        testGradient[atom, p] += 1.0 * np.einsum("pqrs,prqs->", Ppqrs, deriv1_np["TEI" + key])
#        testGradient[atom, p] -= 2.0 * np.einsum("pq,pq->", U[key], Ip)
#
#        Gradient[atom, p] += 1.0 * np.einsum("pq,pq->", Ppq, deriv1_np["T" + key])
#        Gradient[atom, p] += 1.0 * np.einsum("pq,pq->", Ppq, deriv1_np["V" + key])
#        Gradient[atom, p] += 2.0 * np.einsum("pq,pqmm->", Ppq, deriv1_np["TEI" + key][:, :, :nocc, :nocc])
#        Gradient[atom, p] -= 1.0 * np.einsum("pq,pmmq->", Ppq, deriv1_np["TEI" + key][:, :nocc, :nocc, :])
#        Gradient[atom, p] += 1.0 * np.einsum("pqrs,prqs->", Ppqrs, deriv1_np["TEI" + key])
#        Gradient[atom, p] -= 2.0 * np.einsum("pq,pq->", U[key], Ip)
#print("\nSCF Gradient:\n", Gradient)
#print("\ntest Gradient:\n", testGradient)

# Calculate MP2 Gradient with derivative of T2 amplitudes to check derivatives of T2 amplitudes
Gradient = np.zeros((natoms, 3))
for atom in range(natoms):
    for p in range(3):
        key = str(atom) + cart[p]

        # dt2/dx * V Gradient Term:
        Gradient[atom, p] +=  2.0 * np.einsum('ijab,ijab->', deriv1_np["T2" + key], npERI[:nocc, :nocc, nocc:, nocc:])
        Gradient[atom, p] -= 1.0 * np.einsum('ijab,ijba->', deriv1_np["T2" + key], npERI[:nocc, :nocc, nocc:, nocc:])

        # t2 * dV/dx Gradient Term:
        Gradient[atom, p] += 2.0 * np.einsum('ijab,iajb->', t2, deriv1_np["TEI" + key][:nocc, nocc:, :nocc, nocc:])
        Gradient[atom, p] += 2.0 * np.einsum('ijab,ti,tjab->', t2, U[key][:, :nocc], npERI[:, :nocc, nocc:, nocc:])
        Gradient[atom, p] += 2.0 * np.einsum('ijab,tj,itab->', t2, U[key][:, :nocc], npERI[:nocc, :, nocc:, nocc:])
        Gradient[atom, p] += 2.0 * np.einsum('ijab,ta,ijtb->', t2, U[key][:, nocc:], npERI[:nocc, :nocc, :, nocc:])
        Gradient[atom, p] += 2.0 * np.einsum('ijab,tb,ijat->', t2, U[key][:, nocc:], npERI[:nocc, :nocc, nocc:, :])
        Gradient[atom, p] -= 1.0 * np.einsum('ijab,ibja->', t2, deriv1_np["TEI" + key][:nocc, nocc:, :nocc, nocc:])
        Gradient[atom, p] -= 1.0 * np.einsum('ijab,ti,tjba->', t2, U[key][:, :nocc], npERI[:, :nocc, nocc:, nocc:])
        Gradient[atom, p] -= 1.0 * np.einsum('ijab,tj,itba->', t2, U[key][:, :nocc], npERI[:nocc, :, nocc:, nocc:])
        Gradient[atom, p] -= 1.0 * np.einsum('ijab,ta,ijbt->', t2, U[key][:, nocc:], npERI[:nocc, :nocc, nocc:, :])
        Gradient[atom, p] -= 1.0 * np.einsum('ijab,tb,ijta->', t2, U[key][:, nocc:], npERI[:nocc, :nocc, :, nocc:])

#print(np.asarray(mol.nuclear_repulsion_energy_deriv1([0,0,0])))
print("\nGradient Test:\n", scf_Gradient + Gradient)


# Build the response hessian now
for r in range(3 * natoms):
    for c in range(r + 1):
        atom1 = r // 3
        atom2 = c // 3

        p = r % 3
        q = c % 3

        key1  = str(atom1) + cart[p]
        key2  = str(atom2) + cart[q]

        # Ipq Contributions to the Hessian:
        # d^2E/dadB += P+(aB) sum_pq Ipq ( Upt^a * Uqt^B - Spt^a * Sqt^B)
        #
        Hes["R"][r][c] =  1.0 * np.einsum('pq,pt,qt->', I, U[key1], U[key2], optimize=True)
        Hes["R"][r][c] +=  1.0 * np.einsum('pq,pt,qt->', I, U[key2], U[key1], optimize=True)
        #
        Hes["R"][r][c] += -1.0 * np.einsum('pq,pt,qt->', I, deriv1_np["S" + key2], deriv1_np["S" + key1], optimize=True)
        Hes["R"][r][c] += -1.0 * np.einsum('pq,pt,qt->', I, deriv1_np["S" + key1], deriv1_np["S" + key2], optimize=True)
        


        # Ppq Contributions to the Hessian:
        # d^2E/dadB += P+(aB) sum_pq Dpq [ sum_t ( Upt^a * ftq^B + Utq^a * fpt^B ) ] 
        #
        Hes["R"][r][c] += 1.0 * np.einsum('pq,tp,tq->', Ppq, U[key1], F_grad[key2])
        Hes["R"][r][c] += 1.0 * np.einsum('pq,tp,tq->', Ppq, U[key2], F_grad[key1])
        Hes["R"][r][c] += 1.0 * np.einsum('pq,tq,pt->', Ppq, U[key1], F_grad[key2])
        Hes["R"][r][c] += 1.0 * np.einsum('pq,tq,pt->', Ppq, U[key2], F_grad[key1])
        #
        # d^2E/dadB += P+(aB) sum_pq Dpq ( sum_tm [ Utm^a * ( <pm||qt>^B + <pt||qm>^B ) ] )
        #
        Hes["R"][r][c] += 2.0 * np.einsum('pq,tm,pqmt->', Ppq, U[key1][:, :nocc], deriv1_np["TEI" + key2][:, :, :nocc, :]) 
        Hes["R"][r][c] -= 1.0 * np.einsum('pq,tm,ptmq->', Ppq, U[key1][:, :nocc], deriv1_np["TEI" + key2][:, :, :nocc, :]) 
        Hes["R"][r][c] += 2.0 * np.einsum('pq,tm,pqtm->', Ppq, U[key1][:, :nocc], deriv1_np["TEI" + key2][:, :, :, :nocc]) 
        Hes["R"][r][c] -= 1.0 * np.einsum('pq,tm,pmtq->', Ppq, U[key1][:, :nocc], deriv1_np["TEI" + key2][:, :nocc, :, :]) 
        Hes["R"][r][c] += 2.0 * np.einsum('pq,tm,pqmt->', Ppq, U[key2][:, :nocc], deriv1_np["TEI" + key1][:, :, :nocc, :]) 
        Hes["R"][r][c] -= 1.0 * np.einsum('pq,tm,ptmq->', Ppq, U[key2][:, :nocc], deriv1_np["TEI" + key1][:, :, :nocc, :]) 
        Hes["R"][r][c] += 2.0 * np.einsum('pq,tm,pqtm->', Ppq, U[key2][:, :nocc], deriv1_np["TEI" + key1][:, :, :, :nocc]) 
        Hes["R"][r][c] -= 1.0 * np.einsum('pq,tm,pmtq->', Ppq, U[key2][:, :nocc], deriv1_np["TEI" + key1][:, :nocc, :, :]) 
        #
        # d^2E/dadB += P+(aB) sum_pq Dpq ( sum_tv [ Utp^a * Uvq^B * ftv ] )
        #
        Hes["R"][r][c] += 1.0 * np.einsum('pq,tp,vq,tv->', Ppq, U[key1], U[key2], F)
        Hes["R"][r][c] += 1.0 * np.einsum('pq,tp,vq,tv->', Ppq, U[key2], U[key1], F)
        #
        # d^2E/dadB += P+(aB) sum_pq Dpq ( sum_tvm [ Utp^a * Uvm^B * ( <tm||qv> + <tv||qm> ) ] )
        #
        Hes["R"][r][c] += 2.0 * np.einsum('pq,tp,vm,tmqv->', Ppq, U[key1], U[key2][:, :nocc], npERI[:, :nocc, :, :])
        Hes["R"][r][c] -= 1.0 * np.einsum('pq,tp,vm,tmvq->', Ppq, U[key1], U[key2][:, :nocc], npERI[:, :nocc, :, :])
        Hes["R"][r][c] += 2.0 * np.einsum('pq,tp,vm,tvqm->', Ppq, U[key1], U[key2][:, :nocc], npERI[:, :, :, :nocc])
        Hes["R"][r][c] -= 1.0 * np.einsum('pq,tp,vm,tvmq->', Ppq, U[key1], U[key2][:, :nocc], npERI[:, :, :nocc, :])
        Hes["R"][r][c] += 2.0 * np.einsum('pq,tp,vm,tmqv->', Ppq, U[key2], U[key1][:, :nocc], npERI[:, :nocc, :, :])
        Hes["R"][r][c] -= 1.0 * np.einsum('pq,tp,vm,tmvq->', Ppq, U[key2], U[key1][:, :nocc], npERI[:, :nocc, :, :])
        Hes["R"][r][c] += 2.0 * np.einsum('pq,tp,vm,tvqm->', Ppq, U[key2], U[key1][:, :nocc], npERI[:, :, :, :nocc])
        Hes["R"][r][c] -= 1.0 * np.einsum('pq,tp,vm,tvmq->', Ppq, U[key2], U[key1][:, :nocc], npERI[:, :, :nocc, :])
        #
        # d^2E/dadB += P+(aB) sum_pq Dpq ( sum_tvm [ Utq^a * Uvm^B * ( <pm||tv> + <pv||tm> ) ] )
        #
        Hes["R"][r][c] += 2.0 * np.einsum('pq,tq,vm,pmtv->', Ppq, U[key1], U[key2][:, :nocc], npERI[:, :nocc, :, :])
        Hes["R"][r][c] -= 1.0 * np.einsum('pq,tq,vm,pmvt->', Ppq, U[key1], U[key2][:, :nocc], npERI[:, :nocc, :, :])
        Hes["R"][r][c] += 2.0 * np.einsum('pq,tq,vm,pvtm->', Ppq, U[key1], U[key2][:, :nocc], npERI[:, :, :, :nocc])
        Hes["R"][r][c] -= 1.0 * np.einsum('pq,tq,vm,pvmt->', Ppq, U[key1], U[key2][:, :nocc], npERI[:, :, :nocc, :])
        Hes["R"][r][c] += 2.0 * np.einsum('pq,tq,vm,pmtv->', Ppq, U[key2], U[key1][:, :nocc], npERI[:, :nocc, :, :])
        Hes["R"][r][c] -= 1.0 * np.einsum('pq,tq,vm,pmvt->', Ppq, U[key2], U[key1][:, :nocc], npERI[:, :nocc, :, :])
        Hes["R"][r][c] += 2.0 * np.einsum('pq,tq,vm,pvtm->', Ppq, U[key2], U[key1][:, :nocc], npERI[:, :, :, :nocc])
        Hes["R"][r][c] -= 1.0 * np.einsum('pq,tq,vm,pvmt->', Ppq, U[key2], U[key1][:, :nocc], npERI[:, :, :nocc, :])
        #
        # d^2E/dadB += P+(aB) sum_pq Dpq ( sum_tvm [ 1/2 * Utm^a * Uvm^B * ( <pt||qv> + <pv||qt> ) ] )
        #
        Hes["R"][r][c] += 0.5 * 2.0 * np.einsum('pq,tm,vm,ptqv->', Ppq, U[key1][:, :nocc], U[key2][:, :nocc], npERI)
        Hes["R"][r][c] -= 0.5 * 1.0 * np.einsum('pq,tm,vm,ptvq->', Ppq, U[key1][:, :nocc], U[key2][:, :nocc], npERI)
        Hes["R"][r][c] += 0.5 * 2.0 * np.einsum('pq,tm,vm,pvqt->', Ppq, U[key1][:, :nocc], U[key2][:, :nocc], npERI)
        Hes["R"][r][c] -= 0.5 * 1.0 * np.einsum('pq,tm,vm,pvtq->', Ppq, U[key1][:, :nocc], U[key2][:, :nocc], npERI)
        Hes["R"][r][c] += 0.5 * 2.0 * np.einsum('pq,tm,vm,ptqv->', Ppq, U[key2][:, :nocc], U[key1][:, :nocc], npERI)
        Hes["R"][r][c] -= 0.5 * 1.0 * np.einsum('pq,tm,vm,ptvq->', Ppq, U[key2][:, :nocc], U[key1][:, :nocc], npERI)
        Hes["R"][r][c] += 0.5 * 2.0 * np.einsum('pq,tm,vm,pvqt->', Ppq, U[key2][:, :nocc], U[key1][:, :nocc], npERI)
        Hes["R"][r][c] -= 0.5 * 1.0 * np.einsum('pq,tm,vm,pvtq->', Ppq, U[key2][:, :nocc], U[key1][:, :nocc], npERI)


        
        # Ppqrs Contributions to the Hessian:
        # d^2E/dadB += P+(aB) sum_pqrs ( sum_t [ Utp^a * <tq|rs>^B + Utq^a * <pt|rs>^B + Utr^a * <pq|ts>^B + Uts^a * <pq|rt>^B ] )
        #
        Hes["R"][r][c] += 1.0 * np.einsum('pqrs,tp,trqs->', Ppqrs, U[key1], deriv1_np["TEI" + key2])
        Hes["R"][r][c] += 1.0 * np.einsum('pqrs,tq,prts->', Ppqrs, U[key1], deriv1_np["TEI" + key2])
        Hes["R"][r][c] += 1.0 * np.einsum('pqrs,tr,ptqs->', Ppqrs, U[key1], deriv1_np["TEI" + key2])
        Hes["R"][r][c] += 1.0 * np.einsum('pqrs,ts,prqt->', Ppqrs, U[key1], deriv1_np["TEI" + key2])
        Hes["R"][r][c] += 1.0 * np.einsum('pqrs,tp,trqs->', Ppqrs, U[key2], deriv1_np["TEI" + key1])
        Hes["R"][r][c] += 1.0 * np.einsum('pqrs,tq,prts->', Ppqrs, U[key2], deriv1_np["TEI" + key1])
        Hes["R"][r][c] += 1.0 * np.einsum('pqrs,tr,ptqs->', Ppqrs, U[key2], deriv1_np["TEI" + key1])
        Hes["R"][r][c] += 1.0 * np.einsum('pqrs,ts,prqt->', Ppqrs, U[key2], deriv1_np["TEI" + key1])
        #
        # d^2E/dadB += P+(aB) sum_pqrs ( sum_tv [ Utp^a * Uvq^B * <tv|rs> + Utp^a * Uvr^B * <tq|vs> + Utp^a * Uvs^B * <tq|rv> + ...
        #                                   ... + Utq^a * Uvr^B * <pt|vs> + Utq^a * Uvs^B * <pt|rv> + Utr^a * Uvs^B * <pq|tv> ] )
        #
        Hes["R"][r][c] += 1.0 * np.einsum('pqrs,tp,vq,tvrs->', Ppqrs, U[key1], U[key2], npERI)
        Hes["R"][r][c] += 1.0 * np.einsum('pqrs,tp,vr,tqvs->', Ppqrs, U[key1], U[key2], npERI)
        Hes["R"][r][c] += 1.0 * np.einsum('pqrs,tp,vs,tqrv->', Ppqrs, U[key1], U[key2], npERI)
        Hes["R"][r][c] += 1.0 * np.einsum('pqrs,tq,vr,ptvs->', Ppqrs, U[key1], U[key2], npERI)
        Hes["R"][r][c] += 1.0 * np.einsum('pqrs,tq,vs,ptrv->', Ppqrs, U[key1], U[key2], npERI)
        Hes["R"][r][c] += 1.0 * np.einsum('pqrs,tr,vs,pqtv->', Ppqrs, U[key1], U[key2], npERI)
        Hes["R"][r][c] += 1.0 * np.einsum('pqrs,tp,vq,tvrs->', Ppqrs, U[key2], U[key1], npERI)
        Hes["R"][r][c] += 1.0 * np.einsum('pqrs,tp,vr,tqvs->', Ppqrs, U[key2], U[key1], npERI)
        Hes["R"][r][c] += 1.0 * np.einsum('pqrs,tp,vs,tqrv->', Ppqrs, U[key2], U[key1], npERI)
        Hes["R"][r][c] += 1.0 * np.einsum('pqrs,tq,vr,ptvs->', Ppqrs, U[key2], U[key1], npERI)
        Hes["R"][r][c] += 1.0 * np.einsum('pqrs,tq,vs,ptrv->', Ppqrs, U[key2], U[key1], npERI)
        Hes["R"][r][c] += 1.0 * np.einsum('pqrs,tr,vs,pqtv->', Ppqrs, U[key2], U[key1], npERI)



        ## dPpq/da Contibutions to the Hessian:
        ## d^2E/dadB += 1/2 P+(aB) sum_pq dPpq/da ( fpq^B + Uqp^B * fqq + Upq^B * fpp + sum_tm [ Utm^B * <pm||qt> + <pt||qm> ] )
        ##
        #Hes["R"][r][c] += 0.5 * 1.0 * np.einsum('pq,pq->', dPpq[key1], F_grad[key2])
        #Hes["R"][r][c] += 0.5 * 1.0 * np.einsum('pq,pq->', dPpq[key2], F_grad[key1])

        #Hes["R"][r][c] += 0.5 * 1.0 * np.einsum('pq,qp,qq->', dPpq[key1], U[key2], F)
        #Hes["R"][r][c] += 0.5 * 1.0 * np.einsum('pq,pq,pp->', dPpq[key1], U[key2], F)
        #Hes["R"][r][c] += 0.5 * 1.0 * np.einsum('pq,qp,qq->', dPpq[key2], U[key1], F)
        #Hes["R"][r][c] += 0.5 * 1.0 * np.einsum('pq,pq,pp->', dPpq[key2], U[key1], F)

        #Hes["R"][r][c] += 0.5 * 2.0 * np.einsum('pq,tm,pmqt->', dPpq[key1], U[key2][:, :nocc], npERI[:, :nocc, :, :])
        #Hes["R"][r][c] -= 0.5 * 1.0 * np.einsum('pq,tm,pmtq->', dPpq[key1], U[key2][:, :nocc], npERI[:, :nocc, :, :])
        #Hes["R"][r][c] += 0.5 * 2.0 * np.einsum('pq,tm,ptqm->', dPpq[key1], U[key2][:, :nocc], npERI[:, :, :, :nocc])
        #Hes["R"][r][c] -= 0.5 * 1.0 * np.einsum('pq,tm,ptmq->', dPpq[key1], U[key2][:, :nocc], npERI[:, :, :nocc, :])
        #Hes["R"][r][c] += 0.5 * 2.0 * np.einsum('pq,tm,pmqt->', dPpq[key2], U[key1][:, :nocc], npERI[:, :nocc, :, :])
        #Hes["R"][r][c] -= 0.5 * 1.0 * np.einsum('pq,tm,pmtq->', dPpq[key2], U[key1][:, :nocc], npERI[:, :nocc, :, :])
        #Hes["R"][r][c] += 0.5 * 2.0 * np.einsum('pq,tm,ptqm->', dPpq[key2], U[key1][:, :nocc], npERI[:, :, :, :nocc])
        #Hes["R"][r][c] -= 0.5 * 1.0 * np.einsum('pq,tm,ptmq->', dPpq[key2], U[key1][:, :nocc], npERI[:, :, :nocc, :])



        ##dPpqrs/da Contributions to the Hessian:
        ##  d^2E/dadB += 1/2 P+(aB) sum_pqrs dPpqrs/da ( <pq|rs>^B + sum_t [ Utp^B * <tq|rs> + Utq^B * <pt|rs> + Utr^B * <pq|ts> + Uts^B * <pq|rt> ] )
        ##
        #Hes["R"][r][c] += 0.5 * 1.0 * np.einsum('pqrs,prqs->', dPpqrs[key1], deriv1_np["TEI" + key2])
        #Hes["R"][r][c] += 0.5 * 1.0 * np.einsum('pqrs,prqs->', dPpqrs[key2], deriv1_np["TEI" + key1])

        #Hes["R"][r][c] += 0.5 * 1.0 * np.einsum('pqrs,tp,tqrs->', dPpqrs[key1], U[key2], npERI)
        #Hes["R"][r][c] += 0.5 * 1.0 * np.einsum('pqrs,tq,ptrs->', dPpqrs[key1], U[key2], npERI)
        #Hes["R"][r][c] += 0.5 * 1.0 * np.einsum('pqrs,tr,pqts->', dPpqrs[key1], U[key2], npERI)
        #Hes["R"][r][c] += 0.5 * 1.0 * np.einsum('pqrs,ts,pqrt->', dPpqrs[key1], U[key2], npERI)
        #Hes["R"][r][c] += 0.5 * 1.0 * np.einsum('pqrs,tp,tqrs->', dPpqrs[key2], U[key1], npERI)
        #Hes["R"][r][c] += 0.5 * 1.0 * np.einsum('pqrs,tq,ptrs->', dPpqrs[key2], U[key1], npERI)
        #Hes["R"][r][c] += 0.5 * 1.0 * np.einsum('pqrs,tr,pqts->', dPpqrs[key2], U[key1], npERI)
        #Hes["R"][r][c] += 0.5 * 1.0 * np.einsum('pqrs,ts,pqrt->', dPpqrs[key2], U[key1], npERI)



        Hes["R"][c][r] = Hes["R"][r][c]


Mat = psi4.core.Matrix.from_array(Hes["R"])
Mat.name = " RESPONSE HESSIAN"
Mat.print_out()

for key in Hes:
    Hessian += Hes[key]

Mat = psi4.core.Matrix.from_array(Hessian)
Mat.name = " TOTAL HESSIAN"
Mat.print_out()

##H_psi4 = psi4.core.Matrix.from_list([
##[ 0.07613952484989, 0.00000000000000, 0.00000000000000,-0.03806976242497, 0.00000000000000,-0.00000000000000,-0.03806976242497,-0.00000000000000, 0.00000000000000],
##[ 0.00000000000000, 0.48290536165172,-0.00000000000000,-0.00000000000000,-0.24145268082589, 0.15890015082364, 0.00000000000000,-0.24145268082590,-0.15890015082364],
##[ 0.00000000000000,-0.00000000000000, 0.43734495429393,-0.00000000000000, 0.07344233387869,-0.21867247714697,-0.00000000000000,-0.07344233387869,-0.21867247714697],
##[-0.03806976242497,-0.00000000000000,-0.00000000000000, 0.04537741867538,-0.00000000000000, 0.00000000000000,-0.00730765625041, 0.00000000000000,-0.00000000000000],
##[ 0.00000000000000,-0.24145268082589, 0.07344233387869,-0.00000000000000, 0.25786500091002,-0.11617124235117, 0.00000000000000,-0.01641232008412, 0.04272890847247],
##[-0.00000000000000, 0.15890015082364,-0.21867247714697, 0.00000000000000,-0.11617124235117, 0.19775197798054, 0.00000000000000,-0.04272890847247, 0.02092049916645],
##[-0.03806976242497, 0.00000000000000,-0.00000000000000,-0.00730765625041, 0.00000000000000, 0.00000000000000, 0.04537741867538,-0.00000000000000, 0.00000000000000],
##[-0.00000000000000,-0.24145268082590,-0.07344233387869, 0.00000000000000,-0.01641232008412,-0.04272890847247,-0.00000000000000, 0.25786500091002, 0.11617124235117],
##[ 0.00000000000000,-0.15890015082364,-0.21867247714697,-0.00000000000000, 0.04272890847247, 0.02092049916645, 0.00000000000000, 0.11617124235117, 0.19775197798054]
##])
#
#H_psi4 = psi4.core.Matrix.from_list([
#[-3.5884988758,-0.0000000000,-0.0000000000, 1.7942494379, 0.0000000000, 0.0000000000, 1.7942494379,-0.0000000000, 0.0000000000],
#[-0.0000000000, 8.4614335110,-0.0000000000,-0.0000000000,-4.2307167555, 4.7072194871, 0.0000000000,-4.2307167555,-4.7072194871],
#[-0.0000000000,-0.0000000000, 3.7953179675, 0.0000000000, 4.3768046756,-1.8976589837, 0.0000000000,-4.3768046756,-1.8976589837],
#[ 1.7942494379,-0.0000000000, 0.0000000000,-1.8654873342,-0.0000000000,-0.0000000000, 0.0712378963, 0.0000000000,-0.0000000000],
#[ 0.0000000000,-4.2307167555, 4.3768046756,-0.0000000000, 4.3786082939,-4.5420120813,-0.0000000000,-0.1478915384, 0.1652074058],
#[ 0.0000000000, 4.7072194871,-1.8976589837,-0.0000000000,-4.5420120813, 1.8193083397,-0.0000000000,-0.1652074058, 0.0783506441],
#[ 1.7942494379, 0.0000000000, 0.0000000000, 0.0712378963,-0.0000000000,-0.0000000000,-1.8654873342, 0.0000000000,-0.0000000000],
#[-0.0000000000,-4.2307167555,-4.3768046756, 0.0000000000,-0.1478915384,-0.1652074058, 0.0000000000, 4.3786082939, 4.5420120813],
#[ 0.0000000000,-4.7072194871,-1.8976589837,-0.0000000000, 0.1652074058, 0.0783506441,-0.0000000000, 4.5420120813, 1.8193083397]
#])
#
#H_python_mat = psi4.core.Matrix.from_array(Hessian)
#psi4.compare_matrices(H_psi4, H_python_mat, 10, "RHF-HESSIAN-TEST")
