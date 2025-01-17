from rdkit import Chem
import torch
from dgllife.utils import *
from dgllife.utils.featurizers import CanonicalAtomFeaturizer, CanonicalBondFeaturizer
from ocelotml_2d.mlp_features import *
import json
from ocelotml_2d.MPNN_evidential import  MPNNPredictor_evidential
import dgl


DEVICE = 'cpu'

# function to predict from smiles
def make_prediction_with_smiles(smiles, model_name="vie_4gen_evi"):
    path = "models/ocelotml_2d/{}".format(model_name)
    d = {"params_file" : "{}/params.json".format(path),
     "chk_file" : "{}/best_r2.pt".format(path)
     }
    inputs = model_input_from_smiles(smiles,concat_feats="rdkit",fp=False, dft_descriptors=None)
    prediction = evaluate(inputs=inputs,**d)
    mean, lam, alpha, beta  = prediction
    inverse_evidence = 1. / ((alpha - 1) * lam)
    var = beta * inverse_evidence
    with open("{}/params_std.json".format(path)) as f:
        std_scale = json.load(f)
    rescaled_var = var * std_scale["std_recal_ratio"]

    return [round(mean,3), round(rescaled_var,3)]
    # return [round(prediction.tolist()[0][0],2), "eV"]


# generates the model
def define_model(node_in_feats=74,
        edge_in_feats=12,
        node_out_feats=64,
        edge_hidden_feats=128,
        n_tasks=1,
        num_step_message_passing=6,
        num_step_set2set=6,
        num_layer_set2set=3,
        dropout = 0,
        descriptor_feats=0):

    model = MPNNPredictor_evidential(
        node_in_feats=node_in_feats,
        edge_in_feats=edge_in_feats,
        node_out_feats=node_out_feats,
        edge_hidden_feats=edge_hidden_feats,
        n_tasks=n_tasks,
        num_step_message_passing=num_step_message_passing,
        num_step_set2set=num_step_set2set,
        num_layer_set2set=num_layer_set2set,
        dropout=dropout,
        descriptor_feats=descriptor_feats)

    return model

# load the pretrained model and the parameters
def ocelot_model(params_file,chk_file,feats_dim=0):
    with open(params_file, "r") as f:
        params = json.load(f)
    params.update({"descriptor_feats": feats_dim})
    model = define_model(**params)
    model.load_state_dict(torch.load(chk_file, map_location=torch.device('cpu')))
    model.to(DEVICE)
    return model


# molecule descriptors
def get_mol_descriptors(mol, concat_feats, fp=True, dft_descriptors=None):
    descriptors = []
    if concat_feats in ["rdkit", "both"]:
        descriptors.extend(molecule_descriptors(mol, fp))
    if dft_descriptors:
        descriptors.extend(get_labels(mol, dft_descriptors))
    return descriptors


# get the dft labels for concatenating to input
def get_labels(mol, descriptors):
    labels = []
    for desc in descriptors:
        labels.append(float(mol.GetProp(desc)))
    return labels

# generate the bonda and atom features
def model_input_from_mol(mol,concat_feats=None,fp=False, dft_descriptors=None):
    atom_featurizer = CanonicalAtomFeaturizer(atom_data_field="hv")
    bond_featurizer = CanonicalBondFeaturizer(bond_data_field="he")

    # generate the graphs with node and edge features
    graph = mol_to_bigraph(mol,
                                  node_featurizer=atom_featurizer, edge_featurizer=bond_featurizer,
                                  )

    # concatenate feats if needed
    if concat_feats in ["rdkit", "both", "dft"]:
        feats = torch.tensor([get_mol_descriptors(mol, concat_feats, fp, dft_descriptors)])
        feats_dim = feats[0].shape[0]
    else:
        feats_dim = 0
        feats = torch.tensor([])

    return graph, feats, feats_dim,

# featurize the smiles to generate model input
def model_input_from_smiles(smiles,concat_feats=None,fp=False, dft_descriptors=None):
    mol = Chem.MolFromSmiles(smiles)
    return model_input_from_mol(mol,concat_feats,fp, dft_descriptors)


# evaluate
def evaluate(inputs, **model_kwargs):
    g, feats, feats_dim = inputs
    g = g.to(DEVICE)
    ndata = g.ndata["hv"].to(DEVICE)
    edata = g.edata["he"].to(DEVICE)
    feats = feats.to(DEVICE)
    g.set_n_initializer(dgl.init.zero_initializer)
    g.set_e_initializer(dgl.init.zero_initializer)

    model = ocelot_model(feats_dim=feats_dim, **model_kwargs)
    model.eval()

    if feats_dim == 0:
        prediction = model(g, ndata, edata)
    else:
        prediction = model(g, ndata, edata, feats)
    return prediction.tolist()[0]

