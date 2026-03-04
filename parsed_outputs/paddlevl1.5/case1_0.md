Article

# De novo design of luciferases using deep learning

https://doi.org/10.1038/s41586-023-05696-3

Received: 19 January 2022

Accepted: 3 January 2023

Published online: 22 February 2023

Andy Hsien-Wei Yeh $ ^{1,2,3,7} $, Christoffer Norn $ ^{1,2,7} $, Yakov Kipnis $ ^{1,2,4} $, Doug Tischer $ ^{1,2} $, Samuel J. Pellock $ ^{1,2} $, Declan Evans $ ^{5} $, Pengchen Ma $ ^{5,6} $, Gyu Rie Lee $ ^{1,2} $, Jason Z. Zhang $ ^{1,2} $, Ivan Anishchenko $ ^{1,2} $, Brian Coventry $ ^{1,2,4} $, Longxing Cao $ ^{1,2} $, Justas Dauparas $ ^{1,2} $, Samer Halabiya $ ^{2} $, Michelle DeWitt $ ^{2} $, Lauren Carter $ ^{2} $, K. N. Houk $ ^{5} $ & David Baker $ ^{1,2,4} $



Open access

🔒 Check for updates

De novo enzyme design has sought to introduce active sites and substrate-binding pockets that are predicted to catalyse a reaction of interest into geometrically compatible native scaffolds¹,², but has been limited by a lack of suitable protein structures and the complexity of native protein sequence–structure relationships. Here we describe a deep-learning-based ‘family-wide hallucination’ approach that generates large numbers of idealized protein structures containing diverse pocket shapes and designed sequences that encode them. We use these scaffolds to design artificial luciferases that selectively catalyse the oxidative chemiluminescence of the synthetic luciferin substrates diphenylterazine³ and 2-deoxycoelenterazine. The designed active sites position an arginine guanidinium group adjacent to an anion that develops during the reaction in a binding pocket with high shape complementarity. For both luciferin substrates, we obtain designed luciferases with high selectivity; the most active of these is a small (13.9 kDa) and thermostable (with a melting temperature higher than 95 °C) enzyme that has a catalytic efficiency on diphenylterazine ( $ k_{\text{cat}}/K_m = 10^6 \, \text{M}^{-1} \, \text{s}^{-1} $) comparable to that of native luciferases, but a much higher substrate specificity. The creation of highly active and specific biocatalysts from scratch with broad applications in biomedicine is a key milestone for computational enzyme design, and our approach should enable generation of a wide range of luciferases and other enzymes.

Bioluminescent light produced by the enzymatic oxidation of a luciferin substrate by luciferases is widely used for bioassays and imaging in biomedical research. Because no excitation light source is needed, luminescent photons are produced in the dark; this results in higher sensitivity than fluorescence imaging in live animal models and in biological samples in which autofluorescence or phototoxicity is a concern $ ^{4,5} $. However, the development of luciferases as molecular probes has lagged behind that of well-developed fluorescent protein toolkits for a number of reasons: (i) very few native luciferases have been identified $ ^{6,7} $; (ii) many of those that have been identified require multiple disulfide bonds to stabilize the structure and are therefore prone to misfolding in mammalian cells $ ^{8} $; (iii) most native luciferases do not recognize synthetic luciferins with more desirable photophysical properties $ ^{9} $; and (iv) multiplexed imaging to follow multiple processes in parallel using mutually orthogonal luciferase–luciferin pairs has been limited by the low substrate specificity of native luciferases $ ^{10,11} $.

We sought to use de novo protein design to create luciferases that are small, highly stable, well-expressed in cells, specific for one substrate and need no cofactors to function. We chose a synthetic luciferin, diphenylterazine (DTZ), as the target substrate because of its high quantum yield, red-shifted emission $ ^{3} $, favourable in vivo pharmacokinetics $ ^{12,13} $ and lack of required cofactors for light emission. Previous computational enzyme design efforts have primarily repurposed native protein scaffolds in the Protein Data Bank (PDB) $ ^{1,2} $, but there are few native structures with binding pockets appropriate for DTZ, and the effects of sequence changes on native proteins can be unpredictable (designed helical bundles have also been used as enzyme scaffolds $ ^{14-16} $, but these are limited in number and most do not have pockets that are suitable for DTZ binding). To circumvent these limitations, we set out to generate large numbers of small and stable protein scaffolds with pockets of the appropriate size and shape for DTZ, and with clear sequence–structure relationships to facilitate subsequent active-site incorporation. To identify protein folds that are capable of hosting such pockets, we first docked DTZ into 4,000 native small-molecule-binding proteins. We found that many nuclear transport factor 2 (NTF2)-like folds have binding pockets with appropriate shape complementarity and size for DTZ placement (pink dashes in Fig. 1e), and hence selected the NTF2-like superfamily as the target topology.

