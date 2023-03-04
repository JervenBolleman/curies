# -*- coding: utf-8 -*-

"""Identifier mappings service.

This contains an implementation of the service described in `SPARQL-enabled identifier
conversion with Identifiers.org <https://pubmed.ncbi.nlm.nih.gov/25638809/>`_.
The idea here is that you can write a SPARQL query like the following:

.. code-block:: sparql

    PREFIX biomodel: <http://identifiers.org/biomodels.db/>
    PREFIX bqbio: <http://biomodels.net/biology-qualifiers#>
    PREFIX sbmlrdf: <http://identifiers.org/biomodels.vocabulary#>
    PREFIX up: <http://purl.uniprot.org/core/>

    SELECT DISTINCT ?protein ?protein_domain
    WHERE {
        # The first part of this query extracts the proteins appearing in an RDF serialization
        # of the BioModels database (see https://www.ebi.ac.uk/biomodels/BIOMD0000000372) on
        # insulin/glucose feedback. Note that modelers call entities appearing in compartmental
        # models "species", and this does not refer to taxa.
        biomodel:BIOMD0000000372 sbmlrdf:species/bqbio:isVersionOf ?biomodels_protein .

        # The second part of this query maps BioModels protein IRIs to UniProt protein IRIs
        # using service XXX - that's what we're implementing here.
        SERVICE <XXX> {
            ?biomodels_protein owl:sameAs ?uniprot_protein.
        }

        # The third part of this query gets links between UniProt proteins and their
        # domains. Since the service maps between the BioModels query, this only gets
        # us relevant protein domains to the insulin/glucose model.
        SERVICE <http://beta.sparql.uniprot.org/sparql> {
            ?uniprot_protein a up:Protein;
                up:organism taxon:9606;
                rdfs:seeAlso ?protein_domain.
        }
    }

The SPARQL endpoint running at the web address XXX takes in the bound values for `?biomodels_protein`
one at a time and dynamically generates triples with `owl:sameAs` as the predicate mapping and other
equivalent IRIs (based on the definition of the converter) as the objects. This allows for gluing
together multiple services that use different URIs for the same entities - in this example, there
are two ways of referring to UniProt Proteins:

1. The BioModels database example represents a SBML model on insulin-glucose feedback and uses legacy
   Identifiers.org URIs for proteins such as http://identifiers.org/uniprot/P01308.
2. The first-part UniProt database uses its own PURLs such as https://purl.uniprot.org/uniprot/P01308.

.. seealso::

    - Jerven Bolleman's implementation of this service in Java: https://github.com/JervenBolleman/sparql-identifiers
    - Vincent Emonet's `SPARQL endpoint for RDFLib generator <https://github.com/vemonet/rdflib-endpoint>`_
"""

import itertools as itt
from typing import Any, Collection, Iterable, List, Set, Tuple, Union, cast

from rdflib import OWL, Graph, URIRef

from .api import Converter

__all__ = [
    "CURIEServiceGraph",
]


class CURIEServiceGraph(Graph):  # type:ignore
    """A service that implements identifier mapping based on a converter."""

    converter: Converter
    predicates: Set[URIRef]

    def __init__(
        self,
        *args: Any,
        converter: Converter,
        predicates: Union[None, str, List[str]] = None,
        **kwargs: Any,
    ) -> None:
        """Instantiate the graph.

        :param args: Positional arguments to pass to :meth:`rdflib.Graph.__init__`
        :param converter: A converter object
        :param predicates: A predicate or set of predicates. If not given, this service
            will use `owl:sameAs` as a predicate for mapping IRIs.
        :param kwargs: Keyword arguments to pass to :meth:`rdflib.Graph.__init__`

        In the following example, a service graph is instantiated using a small example
        converter, then an example SPARQL query is made directly to show how it makes
        results:

        .. code-block:: python

            from curies import Converter
            from curies.mapping_service import CURIEServiceGraph

            converter = Converter.from_priority_prefix_map(
                {
                    "CHEBI": [
                        "https://www.ebi.ac.uk/chebi/searchId.do?chebiId=",
                        "http://identifiers.org/chebi/",
                        "http://purl.obolibrary.org/obo/CHEBI_",
                    ],
                    "GO": ["http://purl.obolibrary.org/obo/GO_"],
                    "OBO": ["http://purl.obolibrary.org/obo/"],
                    ...,
                }
            )
            graph = CURIEServiceGraph(converter=converter)

            res = graph.query('''
                SELECT ?o WHERE {
                    VALUES ?s {
                        <http://purl.obolibrary.org/obo/CHEBI_1>
                    }
                    ?s owl:sameAs ?o
                }
            ''')


        The results of this are:

        ======================================  =================================================
        subject                                 object
        --------------------------------------  -------------------------------------------------
        http://purl.obolibrary.org/obo/CHEBI_1  http://purl.obolibrary.org/obo/CHEBI_1
        http://purl.obolibrary.org/obo/CHEBI_1  http://identifiers.org/chebi/1
        http://purl.obolibrary.org/obo/CHEBI_1  https://www.ebi.ac.uk/chebi/searchId.do?chebiId=1
        ======================================  =================================================
        """
        self.converter = converter
        if predicates is None:
            self.predicates = {OWL.sameAs}
        elif isinstance(predicates, str):
            self.predicates = {URIRef(predicates)}
        else:
            self.predicates = {URIRef(predicate) for predicate in predicates}
        super().__init__(*args, **kwargs)

    def triples(
        self, triple: Tuple[URIRef, URIRef, URIRef]
    ) -> Iterable[Tuple[URIRef, URIRef, URIRef]]:
        """Generate triples, overriden to dynamically generate mappings based on this graph's converter."""
        subj_query, pred_query, obj_query = triple
        if subj_query is None:
            raise ValueError("This service only works for a defined subject.")
        if pred_query not in self.predicates:
            raise ValueError(
                f"Invalid predicate {pred_query}. This service only works for explicit predicates {self.predicates}"
            )
        if obj_query is not None:
            raise ValueError("This service only works when the object is given as a variable.")

        prefix, identifier = self.converter.parse_uri(subj_query)
        if prefix is None or identifier is None:
            # Unhandled IRI gracefully returns no results
            return

        objects = [
            URIRef(obj)
            for obj in cast(Collection[str], self.converter.expand_pair_all(prefix, identifier))
        ]
        for obj, pred in itt.product(objects, self.predicates):
            yield subj_query, pred, obj