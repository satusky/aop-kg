"""Convert AOP-Wiki AOP-XML exports into keyed JSON and graph CSV tables.

The converter is based on the AopXml 2.7.0 schema:
https://raw.githubusercontent.com/swandle06/AopXml/2.7.0/assets/schema/current.xsd
"""

from __future__ import annotations

import argparse
import csv
import html as html_lib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup

XML_NS = "http://www.aopkb.org/aop-xml"
Q = f"{{{XML_NS}}}"
SCHEMA_URL = "https://raw.githubusercontent.com/swandle06/AopXml/2.7.0/assets/schema/current.xsd"

PRIMARY_FILES = {
    "chemical": "chemicals.json",
    "biological-object": "biological_terms.json",
    "biological-process": "biological_terms.json",
    "biological-action": "biological_terms.json",
    "taxonomy": "taxonomies.json",
    "stressor": "stressors.json",
    "key-event": "key_events.json",
    "key-event-relationship": "key_event_relationships.json",
    "aop": "aops.json",
    "vendor-specific": "vendor_specific.jsonl",
}

LEGACY_CLEANUP_FILES = {
    "chemicals.jsonl",
    "biological_terms.jsonl",
    "taxonomies.jsonl",
    "stressors.jsonl",
    "key_events.jsonl",
    "key_event_relationships.jsonl",
    "aops.jsonl",
    "vendor_specific.json",
}

TEXT_FIELDS_WITH_HTML = {
    "abstract",
    "authors",
    "background",
    "description",
    "development-strategy",
    "evidence-collection-strategy",
    "evidence-supporting-taxonomic-applicability",
    "evidence-supporting-chemical-initiation",
    "known-modulating-factors",
    "measurement-methodology",
    "potential-applications",
    "references",
    "regulatory-relevance",
    "source",
    "support-evidence",
    "uncertainties-or-inconsistencies",
    "weight-of-evidence-summary",
}


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def q(name: str) -> str:
    return f"{Q}{name}"


def normalize_text(value: str | None, *, html: bool = True) -> str | None:
    """Normalize whitespace and remove HTML/XML export artifacts from text.

    AOP-Wiki narrative fields often contain escaped HTML such as ``<p>`` and
    ``&nbsp;``. Some of those fragments occur in fields not explicitly marked as
    rich text in the schema, so this function also auto-detects HTML-like tags
    and decodes HTML entities for every extracted text value.
    """
    if value is None:
        return None
    value = value.replace("\r", " ").replace("\t", " ")
    value = html_lib.unescape(value).replace("\xa0", " ")
    has_markup = bool(re.search(r"</?[A-Za-z][^>]*>", value))
    if html or has_markup:
        value = BeautifulSoup(value, "html.parser").get_text(" ", strip=True)
    value = html_lib.unescape(value).replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def child(element: ET.Element, name: str) -> ET.Element | None:
    return element.find(q(name))


def children(element: ET.Element, name: str) -> list[ET.Element]:
    return list(element.findall(q(name)))


def child_text(element: ET.Element, name: str, *, html: bool | None = None) -> str | None:
    node = child(element, name)
    if node is None:
        return None
    if html is None:
        html = name in TEXT_FIELDS_WITH_HTML
    return normalize_text("".join(node.itertext()), html=html)


def direct_child_text(element: ET.Element, name: str, *, html: bool | None = None) -> str | None:
    node = child(element, name)
    if node is None:
        return None
    if html is None:
        html = name in TEXT_FIELDS_WITH_HTML
    return normalize_text(node.text, html=html)


def text_list(parent: ET.Element | None, item_name: str, *, html: bool = False) -> list[str]:
    if parent is None:
        return []
    values: list[str] = []
    for node in children(parent, item_name):
        value = normalize_text("".join(node.itertext()), html=html)
        if value is not None:
            values.append(value)
    return values


def drop_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: drop_none(v) for k, v in value.items() if v is not None and v != [] and v != {}}
    if isinstance(value, list):
        return [drop_none(v) for v in value if v is not None and v != {}]
    return value


def json_dump(record: dict[str, Any]) -> str:
    return json.dumps(drop_none(record), ensure_ascii=False, sort_keys=True)


def parse_biological_term(element: ET.Element, term_type: str | None = None) -> dict[str, Any]:
    record = {
        "id": element.get("id"),
        "type": term_type or local_name(element.tag),
        "source_id": direct_child_text(element, "source-id", html=False),
        "source": direct_child_text(element, "source", html=False),
        "name": direct_child_text(element, "name", html=False),
        "synonym": direct_child_text(element, "synonym", html=False),
    }
    return drop_none(record)


def parse_property_values(element: ET.Element) -> dict[str, Any]:
    return drop_none(
        {
            "property": direct_child_text(element, "property"),
            "value": direct_child_text(element, "value"),
            "units": direct_child_text(element, "units"),
            "conditions": direct_child_text(element, "conditions"),
            "source": direct_child_text(element, "source"),
        }
    )


def parse_quality_assurance(element: ET.Element | None) -> dict[str, Any] | None:
    if element is None:
        return None
    return drop_none(
        {
            "contributors": text_list(element, "contributor"),
            "reviewers": text_list(element, "reviewer"),
            "seal_of_approval": text_list(element, "seal-of-approval"),
            "last_modified": direct_child_text(element, "last-modified", html=False),
        }
    )


def parse_evidence_element(element: ET.Element) -> dict[str, Any]:
    tag = local_name(element.tag)
    record: dict[str, Any] = {
        "id": element.get("id"),
        "stressor_id": element.get("stressor-id"),
        "taxonomy_id": element.get("taxonomy-id"),
        "chemical_id": element.get("chemical-id"),
        "user_term": element.get("user-term"),
        "key_event_id": element.get("key-event-id"),
        "evidence": direct_child_text(element, "evidence", html=False),
        "uris": text_list(element, "uri"),
    }
    nested_value = direct_child_text(element, tag, html=False)
    if nested_value is not None:
        record["value"] = nested_value
    return drop_none(record)


def parse_applicability(element: ET.Element | None) -> dict[str, Any] | None:
    if element is None:
        return None
    record = {
        "sex": [parse_evidence_element(node) for node in children(element, "sex")],
        "life_stage": [parse_evidence_element(node) for node in children(element, "life-stage")],
        "taxonomy": [parse_evidence_element(node) for node in children(element, "taxonomy")],
        "uris": text_list(element, "uri"),
        "biological_compartment": text_list(element, "biological-compartment"),
        "time_to_manifestation": text_list(element, "time-to-manifestation"),
        "time_to_manifestation_range": text_list(element, "time-to-manifestation-range"),
        "generation": text_list(element, "generation"),
    }
    return drop_none(record)


def parse_chemical(element: ET.Element) -> dict[str, Any]:
    synonyms_node = child(element, "synonyms")
    return drop_none(
        {
            "id": element.get("id"),
            "inchi": direct_child_text(element, "inchi", html=False),
            "casrn": direct_child_text(element, "casrn", html=False),
            "jchem_inchi_key": direct_child_text(element, "jchem-inchi-key", html=False),
            "indigo_inchi_key": direct_child_text(element, "indigo-inchi-key", html=False),
            "iupac_name": direct_child_text(element, "iupac-name", html=False),
            "smiles": direct_child_text(element, "smiles", html=False),
            "preferred_name": direct_child_text(element, "preferred-name", html=False),
            "synonyms": text_list(synonyms_node, "synonym"),
            "formula": direct_child_text(element, "formula", html=False),
            "dsstox_id": direct_child_text(element, "dsstox-id", html=False),
        }
    )


def parse_substance_info(element: ET.Element) -> dict[str, Any]:
    return drop_none(
        {
            "title": direct_child_text(element, "title"),
            "id": direct_child_text(element, "id"),
            "types": text_list(element, "type"),
            "iuc_names": text_list(element, "iuc-name"),
            "properties": [parse_property_values(node) for node in children(element, "substance-properties")],
        }
    )


def parse_stressor(element: ET.Element) -> dict[str, Any]:
    chemicals_node = child(element, "chemicals")
    return drop_none(
        {
            "id": element.get("id"),
            "name": direct_child_text(element, "name"),
            "quality_assurance": parse_quality_assurance(child(element, "quality-assurance")),
            "description": child_text(element, "description"),
            "chemicals": [parse_evidence_element(node) for node in children(chemicals_node, "chemical-initiator")]
            if chemicals_node is not None
            else [],
            "structural_properties": [parse_property_values(node) for node in children(element, "structural-properties")],
            "synonyms": text_list(element, "synonym"),
            "associated_substances": [parse_substance_info(node) for node in children(element, "associated-substances")],
            "links": text_list(element, "link"),
            "exposure_characterization": child_text(element, "exposure-characterization"),
            "creation_timestamp": direct_child_text(element, "creation-timestamp", html=False),
            "last_modification_timestamp": direct_child_text(element, "last-modification-timestamp", html=False),
        }
    )


def parse_key_event(element: ET.Element) -> dict[str, Any]:
    biological_events_node = child(element, "biological-events")
    stressors_node = child(element, "key-event-stressors")
    return drop_none(
        {
            "id": element.get("id"),
            "title": direct_child_text(element, "title"),
            "short_name": direct_child_text(element, "short-name"),
            "biological_organization_level": direct_child_text(element, "biological-organization-level", html=False),
            "description": child_text(element, "description"),
            "measurement_methodology": child_text(element, "measurement-methodology"),
            "evidence_supporting_taxonomic_applicability": child_text(element, "evidence-supporting-taxonomic-applicability"),
            "organ_term": parse_biological_term(child(element, "organ-term"), "organ-term") if child(element, "organ-term") is not None else None,
            "cell_term": parse_biological_term(child(element, "cell-term"), "cell-term") if child(element, "cell-term") is not None else None,
            "applicability": parse_applicability(child(element, "applicability")),
            "associated_tests": child_text(element, "associated-tests"),
            "biological_events": [drop_none({**node.attrib}) for node in children(biological_events_node, "biological-event")]
            if biological_events_node is not None
            else [],
            "key_event_stressors": [parse_evidence_element(node) for node in children(stressors_node, "key-event-stressor")]
            if stressors_node is not None
            else [],
            "references": child_text(element, "references"),
            "source": direct_child_text(element, "source", html=False),
            "source_internal_id": direct_child_text(element, "source-internal-id", html=False),
            "uri": direct_child_text(element, "uri", html=False),
            "quality_assurance": parse_quality_assurance(child(element, "quality-assurance")),
            "creation_timestamp": direct_child_text(element, "creation-timestamp", html=False),
            "last_modification_timestamp": direct_child_text(element, "last-modification-timestamp", html=False),
        }
    )


def parse_weight_of_evidence(element: ET.Element | None) -> dict[str, Any] | None:
    if element is None:
        return None
    return drop_none(
        {
            "value": direct_child_text(element, "value"),
            "biological_plausibility": child_text(element, "biological-plausibility"),
            "empirical_support_linkage": child_text(element, "emperical-support-linkage"),
            "uncertainties_or_inconsistencies": child_text(element, "uncertainties-or-inconsistencies"),
        }
    )


def parse_quantitative_understanding(element: ET.Element | None) -> dict[str, Any] | None:
    if element is None:
        return None
    return drop_none(
        {
            "description": child_text(element, "description"),
            "support_evidence": child_text(element, "support-evidence"),
            "response_response_relationship": child_text(element, "response-response-relationship"),
            "time_scale": child_text(element, "time-scale"),
            "feedforward_feedback_loops": child_text(element, "feedforward-feedback-loops"),
        }
    )


def parse_key_event_relationship(element: ET.Element) -> dict[str, Any]:
    title = child(element, "title")
    return drop_none(
        {
            "id": element.get("id"),
            "upstream_id": direct_child_text(title, "upstream-id", html=False) if title is not None else None,
            "downstream_id": direct_child_text(title, "downstream-id", html=False) if title is not None else None,
            "detail_level": direct_child_text(title, "detail-level", html=False) if title is not None else None,
            "description": child_text(element, "description"),
            "evidence_collection_strategy": child_text(element, "evidence-collection-strategy"),
            "weight_of_evidence": parse_weight_of_evidence(child(element, "weight-of-evidence")),
            "known_modulating_factors": child_text(element, "known-modulating-factors"),
            "quantitative_understanding": parse_quantitative_understanding(child(element, "quantitative-understanding")),
            "applicability": parse_applicability(child(element, "applicability")),
            "evidence_supporting_taxonomic_applicability": child_text(element, "evidence-supporting-taxonomic-applicability"),
            "references": child_text(element, "references"),
            "source": direct_child_text(element, "source", html=False),
            "source_internal_id": direct_child_text(element, "source-internal-id", html=False),
            "uri": direct_child_text(element, "uri", html=False),
            "quality_assurance": parse_quality_assurance(child(element, "quality-assurance")),
            "creation_timestamp": direct_child_text(element, "creation-timestamp", html=False),
            "last_modification_timestamp": direct_child_text(element, "last-modification-timestamp", html=False),
        }
    )


def parse_url_link(element: ET.Element) -> dict[str, Any]:
    return drop_none(
        {
            "link_source": direct_child_text(element, "link_source"),
            "url": direct_child_text(element, "url", html=False),
            "title": direct_child_text(element, "title"),
        }
    )


def parse_aop_relationship(element: ET.Element) -> dict[str, Any]:
    return drop_none(
        {
            "id": element.get("id"),
            "adjacency": direct_child_text(element, "adjacency", html=False),
            "quantitative_understanding_value": direct_child_text(element, "quantitative-understanding-value", html=False),
            "evidence": direct_child_text(element, "evidence", html=False),
        }
    )


def parse_adverse_outcome(element: ET.Element) -> dict[str, Any]:
    return drop_none(
        {
            "key_event_id": element.get("key-event-id"),
            "examples": child_text(element, "examples"),
            "regulatory_relevance": child_text(element, "regulatory-relevance"),
            "organs_affected": [parse_evidence_element(node) | {
                "synonym": direct_child_text(node, "synonym"),
                "scientific_name": direct_child_text(node, "scientific-name"),
            } for node in children(element, "organs-affected")],
        }
    )


def parse_overall_assessment(element: ET.Element | None) -> dict[str, Any] | None:
    if element is None:
        return None
    return drop_none(
        {
            "description": child_text(element, "description"),
            "applicability": child_text(element, "applicability"),
            "key_event_essentiality_summary": child_text(element, "key-event-essentiality-summary"),
            "weight_of_evidence_summary": child_text(element, "weight-of-evidence-summary"),
            "known_modulating_factors": child_text(element, "known-modulating-factors"),
            "quantitative_considerations": child_text(element, "quantitative-considerations"),
            "uris": text_list(element, "uri"),
        }
    )


def parse_aop(element: ET.Element) -> dict[str, Any]:
    key_events_node = child(element, "key-events")
    relationships_node = child(element, "key-event-relationships")
    stressors_node = child(element, "aop-stressors")
    external_links_node = child(element, "external_links")
    status_node = child(element, "status")
    coaches_node = child(element, "coaches")
    return drop_none(
        {
            "id": element.get("id"),
            "title": direct_child_text(element, "title"),
            "short_name": direct_child_text(element, "short-name"),
            "point_of_contact": direct_child_text(element, "point-of-contact"),
            "authors": child_text(element, "authors"),
            "coaches": text_list(coaches_node, "coach") if coaches_node is not None else [],
            "external_links": [parse_url_link(node) for node in children(external_links_node, "url_link")]
            if external_links_node is not None
            else [],
            "status": {
                "wiki_license": direct_child_text(status_node, "wiki-license", html=False) if status_node is not None else None,
                "oecd_status": direct_child_text(status_node, "oecd-status", html=False) if status_node is not None else None,
            },
            "oecd_project": direct_child_text(element, "oecd-project"),
            "handbook_version": direct_child_text(element, "handbook-version"),
            "abstract": child_text(element, "abstract"),
            "background": child_text(element, "background"),
            "development_strategy": child_text(element, "development-strategy"),
            "molecular_initiating_events": [
                drop_none(
                    {
                        "key_event_id": node.get("key-event-id"),
                        "evidence_supporting_chemical_initiation": child_text(node, "evidence-supporting-chemical-initiation"),
                    }
                )
                for node in children(element, "molecular-initiating-event")
            ],
            "key_event_ids": [node.get("key-event-id") for node in children(key_events_node, "key-event")]
            if key_events_node is not None
            else [],
            "adverse_outcomes": [parse_adverse_outcome(node) for node in children(element, "adverse-outcome")],
            "relationships": [parse_aop_relationship(node) for node in children(relationships_node, "relationship")]
            if relationships_node is not None
            else [],
            "applicability": parse_applicability(child(element, "applicability")),
            "overall_assessment": parse_overall_assessment(child(element, "overall-assessment")),
            "potential_applications": child_text(element, "potential-applications"),
            "aop_stressors": [parse_evidence_element(node) for node in children(stressors_node, "aop-stressor")]
            if stressors_node is not None
            else [],
            "references": child_text(element, "references"),
            "source": direct_child_text(element, "source", html=False),
            "source_internal_id": direct_child_text(element, "source-internal-id", html=False),
            "uris": text_list(element, "uri"),
            "quality_assurance": parse_quality_assurance(child(element, "quality-assurance")),
            "creation_timestamp": direct_child_text(element, "creation-timestamp", html=False),
            "last_modification_timestamp": direct_child_text(element, "last-modification-timestamp", html=False),
        }
    )


def parse_vendor_specific(element: ET.Element) -> dict[str, Any]:
    return drop_none({"id": element.get("id"), "name": element.get("name"), "version": element.get("version")})


def parse_record(element: ET.Element) -> dict[str, Any] | None:
    tag = local_name(element.tag)
    if tag == "chemical":
        return parse_chemical(element)
    if tag in {"biological-object", "biological-process", "biological-action", "taxonomy"}:
        return parse_biological_term(element, tag)
    if tag == "stressor":
        return parse_stressor(element)
    if tag == "key-event":
        return parse_key_event(element)
    if tag == "key-event-relationship":
        return parse_key_event_relationship(element)
    if tag == "aop":
        return parse_aop(element)
    if tag == "vendor-specific":
        return parse_vendor_specific(element)
    return None


def first_uri(record: dict[str, Any]) -> str:
    uri = record.get("uri")
    if isinstance(uri, str):
        return uri
    uris = record.get("uris")
    if isinstance(uris, list) and uris:
        return str(uris[0])
    return ""


def node_from_record(kind: str, record: dict[str, Any], modifiers: Iterable[str] | None = None) -> dict[str, str]:
    if kind == "chemical":
        label = "Chemical"
        name = record.get("preferred_name") or record.get("casrn") or record.get("id")
        source_id = record.get("dsstox_id") or record.get("casrn")
        source = "DSSTox" if record.get("dsstox_id") else None
    elif kind in {"biological-object", "biological-process", "biological-action"}:
        label = "BiologicalTerm"
        name = record.get("name") or record.get("id")
        source = record.get("source")
        source_id = record.get("source_id")
    elif kind == "taxonomy":
        label = "Taxon"
        name = record.get("name") or record.get("id")
        source = record.get("source")
        source_id = record.get("source_id")
    elif kind == "stressor":
        label = "Stressor"
        name = record.get("name") or record.get("id")
        source = None
        source_id = None
    elif kind == "key-event":
        label = "KeyEvent"
        name = record.get("short_name") or record.get("title") or record.get("id")
        source = record.get("source")
        source_id = record.get("source_internal_id")
    elif kind == "key-event-relationship":
        label = "KeyEventRelationship"
        name = f"{record.get('upstream_id', '')} -> {record.get('downstream_id', '')}".strip(" ->") or record.get("id")
        source = record.get("source")
        source_id = record.get("source_internal_id")
    elif kind == "aop":
        label = "AOP"
        name = record.get("short_name") or record.get("title") or record.get("id")
        source = record.get("source")
        source_id = record.get("source_internal_id")
    else:
        label = kind
        name = record.get("name") or record.get("title") or record.get("id")
        source = None
        source_id = None
    return {
        "id": record.get("id", ""),
        "label": label,
        "type": kind,
        "name": name or "",
        "source": source or "",
        "source_id": source_id or "",
        "uri": first_uri(record),
        "modifiers": json.dumps(sorted(set(modifiers or [])), ensure_ascii=False),
    }


def add_node_modifier(node: dict[str, str], modifier: str) -> None:
    try:
        modifiers = set(json.loads(node.get("modifiers") or "[]"))
    except json.JSONDecodeError:
        modifiers = set()
    modifiers.add(modifier)
    node["modifiers"] = json.dumps(sorted(modifiers), ensure_ascii=False)


def edge(source: str | None, target: str | None, edge_type: str, *, edge_id: str | None = None, label: str | None = None, **props: Any) -> dict[str, str] | None:
    if not source or not target:
        return None
    props = drop_none(props)
    return {
        "source": source,
        "target": target,
        "type": edge_type,
        "id": edge_id or "",
        "label": label or edge_type,
        "properties_json": json.dumps(props, ensure_ascii=False, sort_keys=True) if props else "",
    }


def copy_edge_with_target(edge_record: dict[str, str], target: str, **extra_props: Any) -> dict[str, str]:
    copied = dict(edge_record)
    copied["target"] = target
    props = parse_json_object(copied.get("properties_json"))
    props.update(drop_none(extra_props))
    copied["properties_json"] = json.dumps(props, ensure_ascii=False, sort_keys=True) if props else ""
    return copied


def parse_json_object(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def taxonomy_edges(owner_id: str, applicability: dict[str, Any] | None, relation: str) -> Iterable[dict[str, str]]:
    if not applicability:
        return []
    out = []
    for item in applicability.get("taxonomy", []):
        e = edge(owner_id, item.get("taxonomy_id"), relation, evidence=item.get("evidence"), uris=item.get("uris"))
        if e:
            out.append(e)
    return out


def edges_from_record(kind: str, record: dict[str, Any]) -> list[dict[str, str]]:
    record_id = record.get("id")
    out: list[dict[str, str]] = []

    if kind == "stressor":
        for chem in record.get("chemicals", []):
            e = edge(record_id, chem.get("chemical_id"), "HAS_CHEMICAL_INITIATOR", user_term=chem.get("user_term"))
            if e:
                out.append(e)

    elif kind == "key-event":
        for event_item in record.get("biological_events", []):
            for attr, rel in (
                ("object-id", "HAS_BIOLOGICAL_OBJECT"),
                ("process-id", "HAS_BIOLOGICAL_PROCESS"),
                ("action-id", "HAS_BIOLOGICAL_ACTION"),
            ):
                e = edge(record_id, event_item.get(attr), rel)
                if e:
                    out.append(e)
        for item in record.get("key_event_stressors", []):
            e = edge(record_id, item.get("stressor_id"), "HAS_STRESSOR", evidence=item.get("evidence"), uris=item.get("uris"))
            if e:
                out.append(e)
        out.extend(taxonomy_edges(record_id, record.get("applicability"), "APPLIES_TO_TAXON"))

    elif kind == "key-event-relationship":
        e = edge(record.get("upstream_id"), record.get("downstream_id"), "KEY_EVENT_RELATIONSHIP", edge_id=record_id, detail_level=record.get("detail_level"), weight_of_evidence=record.get("weight_of_evidence"))
        if e:
            out.append(e)
        e = edge(record_id, record.get("upstream_id"), "HAS_UPSTREAM_KEY_EVENT")
        if e:
            out.append(e)
        e = edge(record_id, record.get("downstream_id"), "HAS_DOWNSTREAM_KEY_EVENT")
        if e:
            out.append(e)
        out.extend(taxonomy_edges(record_id, record.get("applicability"), "APPLIES_TO_TAXON"))

    elif kind == "aop":
        for mie in record.get("molecular_initiating_events", []):
            e = edge(record_id, mie.get("key_event_id"), "HAS_MOLECULAR_INITIATING_EVENT", evidence_supporting_chemical_initiation=mie.get("evidence_supporting_chemical_initiation"))
            if e:
                out.append(e)
        for key_event_id in record.get("key_event_ids", []):
            e = edge(record_id, key_event_id, "HAS_KEY_EVENT")
            if e:
                out.append(e)
        for ao in record.get("adverse_outcomes", []):
            e = edge(record_id, ao.get("key_event_id"), "HAS_ADVERSE_OUTCOME", examples=ao.get("examples"), regulatory_relevance=ao.get("regulatory_relevance"))
            if e:
                out.append(e)
        for rel in record.get("relationships", []):
            e = edge(record_id, rel.get("id"), "HAS_KEY_EVENT_RELATIONSHIP", adjacency=rel.get("adjacency"), quantitative_understanding_value=rel.get("quantitative_understanding_value"), evidence=rel.get("evidence"))
            if e:
                out.append(e)
        for item in record.get("aop_stressors", []):
            e = edge(record_id, item.get("stressor_id"), "HAS_STRESSOR", evidence=item.get("evidence"), uris=item.get("uris"))
            if e:
                out.append(e)
        out.extend(taxonomy_edges(record_id, record.get("applicability"), "APPLIES_TO_TAXON"))

    return out


def top_level_elements(xml_path: Path) -> Iterable[ET.Element]:
    depth = 0
    for event, element in ET.iterparse(xml_path, events=("start", "end")):
        if event == "start":
            depth += 1
            continue
        if depth == 2:
            yield element
            element.clear()
        depth -= 1


def convert(xml_path: Path, out_dir: Path) -> Counter[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    counts: Counter[str] = Counter()
    output_files = sorted(set(PRIMARY_FILES.values()))
    records_by_file: dict[str, dict[str, dict[str, Any]]] = {filename: {} for filename in output_files if filename.endswith(".json")}
    jsonl_handles: dict[str, Any] = {filename: (out_dir / filename).open("w", encoding="utf-8") for filename in output_files if filename.endswith(".jsonl")}

    nodes: dict[str, dict[str, str]] = {}
    edges: list[dict[str, str]] = []
    stressor_records: dict[str, dict[str, Any]] = {}

    for filename in LEGACY_CLEANUP_FILES:
        (out_dir / filename).unlink(missing_ok=True)

    try:
        for element in top_level_elements(xml_path):
            kind = local_name(element.tag)
            record = parse_record(element)
            if record is None:
                continue
            counts[kind] += 1
            filename = PRIMARY_FILES[kind]
            record_id = record.get("id")
            if not record_id:
                raise ValueError(f"Parsed {kind} record without an id")
            if filename.endswith(".jsonl"):
                jsonl_handles[filename].write(json_dump(record) + "\n")
            else:
                if record_id in records_by_file[filename]:
                    raise ValueError(f"Duplicate id {record_id!r} in {filename}")
                records_by_file[filename][str(record_id)] = drop_none(record)

            if kind == "vendor-specific":
                continue
            if kind == "stressor":
                stressor_records[str(record_id)] = record
                continue
            if kind == "key-event-relationship":
                edges.extend(edges_from_record(kind, record))
                continue
            nodes[str(record_id)] = node_from_record(kind, record)
            edges.extend(edges_from_record(kind, record))
    finally:
        for handle in jsonl_handles.values():
            handle.close()

    stressor_targets: dict[str, list[str]] = {}
    for stressor_id, stressor in stressor_records.items():
        chemical_ids = sorted({str(item.get("chemical_id")) for item in stressor.get("chemicals", []) if item.get("chemical_id") in nodes})
        stressor_targets[stressor_id] = chemical_ids
        if chemical_ids:
            for chemical_id in chemical_ids:
                add_node_modifier(nodes[chemical_id], "stressor")
        else:
            nodes[stressor_id] = node_from_record("stressor", stressor, modifiers=["stressor"])

    resolved_edges: list[dict[str, str]] = []
    for edge_record in edges:
        target = edge_record.get("target")
        if edge_record.get("type") == "HAS_STRESSOR" and target in stressor_targets and stressor_targets[target]:
            stressor = stressor_records.get(target, {})
            for chemical_id in stressor_targets[target]:
                resolved_edges.append(copy_edge_with_target(edge_record, chemical_id, original_stressor_id=target, original_stressor_name=stressor.get("name")))
        else:
            resolved_edges.append(edge_record)

    with (out_dir / "nodes.csv").open("w", encoding="utf-8", newline="") as nodes_handle:
        node_writer = csv.DictWriter(nodes_handle, fieldnames=["id", "label", "type", "name", "source", "source_id", "uri", "modifiers"])
        node_writer.writeheader()
        for node_id in sorted(nodes):
            node_writer.writerow(nodes[node_id])

    with (out_dir / "edges.csv").open("w", encoding="utf-8", newline="") as edges_handle:
        edge_writer = csv.DictWriter(edges_handle, fieldnames=["source", "target", "type", "id", "label", "properties_json"])
        edge_writer.writeheader()
        for edge_record in resolved_edges:
            if edge_record.get("source") in nodes and edge_record.get("target") in nodes:
                edge_writer.writerow(edge_record)

    for filename, records in records_by_file.items():
        (out_dir / filename).write_text(json.dumps(records, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    (out_dir / "summary.json").write_text(json.dumps({"schema_url": SCHEMA_URL, "input": str(xml_path), "counts": counts}, indent=2, sort_keys=True), encoding="utf-8")
    return counts


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Convert an AOP-Wiki AOP-XML export into id-keyed JSON entity files and KG CSV edge/node tables.")
    parser.add_argument("xml", type=Path, help="AOP-Wiki XML export, e.g. aop-wiki-xml-2026-04-01.xml")
    parser.add_argument("-o", "--out-dir", type=Path, default=Path("data/aopwiki/converted"), help="Output directory (default: data/aopwiki/converted)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    counts = convert(args.xml, args.out_dir)
    print(f"Wrote converted AOP-Wiki data to {args.out_dir}")
    for key, value in sorted(counts.items()):
        print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
