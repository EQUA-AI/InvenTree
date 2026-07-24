"""
AIMMS Demo Dataset Provider

Provides access to the InvenTree demo dataset for testing workflows
without requiring a live InvenTree instance.

This module loads data from the inventree_data.json file and provides
the same interface as the InvenTree client, allowing workflows to be
tested with realistic manufacturing data.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ai.core.config import get_settings

logger = logging.getLogger(__name__)


class DemoDatasetProvider:
    """
    Provider for InvenTree demo dataset.
    
    Loads the demo dataset JSON and provides query methods that mimic
    the InvenTree API client interface.
    
    Usage:
        provider = DemoDatasetProvider()
        parts = provider.search_parts("motor")
        stock = provider.get_stock_items(part_id=1)
    """
    
    def __init__(self, dataset_path: Path | str | None = None):
        """
        Initialize the demo dataset provider.
        
        Args:
            dataset_path: Path to the inventree_data.json file.
                         If not provided, uses the path from settings.
        """
        settings = get_settings()
        
        if dataset_path is None:
            dataset_path = settings.demo_dataset_json
        
        self.dataset_path = Path(dataset_path)
        self._raw_data: list[dict[str, Any]] | None = None
        self._data: dict[str, list[dict[str, Any]]] | None = None
        self._loaded = False
    
    def _load_data(self) -> None:
        """Load the dataset from JSON file."""
        if self._loaded:
            return
        
        if not self.dataset_path.exists():
            raise FileNotFoundError(
                f"Demo dataset not found at {self.dataset_path}. "
                "Please ensure the inventree-demo-dataset folder is in the project root."
            )
        
        logger.info(f"Loading demo dataset from {self.dataset_path}")
        
        with open(self.dataset_path, "r") as f:
            self._raw_data = json.load(f)
        
        # Transform Django fixtures format to a more usable structure
        # Django fixtures format: [{"model": "app.model", "pk": 1, "fields": {...}}, ...]
        self._data = self._transform_fixtures(self._raw_data)
        
        self._loaded = True
        
        # Log dataset statistics
        stats = self.get_statistics()
        logger.info(
            f"Demo dataset loaded: {stats['parts']} parts, "
            f"{stats['stock_items']} stock items, "
            f"{stats['categories']} categories"
        )
    
    def _transform_fixtures(self, raw_data: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        """
        Transform Django fixtures format to categorized dictionary.
        
        Input format:
            [{"model": "part.part", "pk": 1, "fields": {"name": "...", ...}}, ...]
        
        Output format:
            {
                "part": [{"pk": 1, "name": "...", ...}, ...],
                "stock_stockitem": [...],
                ...
            }
        """
        result: dict[str, list[dict[str, Any]]] = {}
        
        # Model name mappings (django model name -> our key)
        model_mappings = {
            "part.part": "part",
            "part.partcategory": "part_partcategory",
            "part.bomitem": "part_bomitem",
            "stock.stockitem": "stock_stockitem",
            "stock.stocklocation": "stock_stocklocation",
            "company.company": "company_company",
            "company.supplierpart": "company_supplierpart",
            "company.manufacturerpart": "company_manufacturerpart",
            "company.contact": "company_contact",
            "company.address": "company_address",
            "order.purchaseorder": "order_purchaseorder",
            "order.salesorder": "order_salesorder",
        }
        
        for item in raw_data:
            model = item.get("model", "")
            pk = item.get("pk")
            fields = item.get("fields", {})
            
            # Get the key for this model
            key = model_mappings.get(model)
            if key is None:
                # Use the model name as key (replace . with _)
                key = model.replace(".", "_")
            
            if key not in result:
                result[key] = []
            
            # Combine pk and fields into a single dict
            record = {"pk": pk, **fields}
            result[key].append(record)
        
        return result
    
    @property
    def data(self) -> dict[str, list[dict[str, Any]]]:
        """Get the loaded dataset."""
        self._load_data()
        return self._data or {}
    
    def get_statistics(self) -> dict[str, int]:
        """Get statistics about the loaded dataset."""
        self._load_data()
        
        return {
            "parts": len(self.data.get("part", [])),
            "stock_items": len(self.data.get("stock_stockitem", [])),
            "categories": len(self.data.get("part_partcategory", [])),
            "locations": len(self.data.get("stock_stocklocation", [])),
            "companies": len(self.data.get("company_company", [])),
            "bom_items": len(self.data.get("part_bomitem", [])),
            "suppliers": len(self.data.get("company_supplierpart", [])),
        }
    
    # -------------------------------------------------------------------------
    # Part Queries
    # -------------------------------------------------------------------------
    
    def get_parts(self) -> list[dict[str, Any]]:
        """Get all parts from the dataset."""
        return self.data.get("part", [])
    
    def get_part(self, part_id: int) -> dict[str, Any] | None:
        """Get a specific part by ID (pk)."""
        for part in self.get_parts():
            if part.get("pk") == part_id:
                return part
        return None
    
    def create_part(
        self,
        name: str,
        category: int,
        description: str | None = None,
        ipn: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Create a new part in the demo dataset.
        
        Args:
            name: Part name
            category: Category ID
            description: Part description
            ipn: Internal Part Number
            **kwargs: Additional part fields
            
        Returns:
            Created part data with new pk
        """
        # Generate a new unique pk
        existing_pks = [p.get("pk", 0) for p in self.get_parts()]
        new_pk = max(existing_pks) + 1 if existing_pks else 1
        
        new_part = {
            "pk": new_pk,
            "name": name,
            "category": category,
            "description": description or "",
            "IPN": ipn or "",
            "active": True,
            "virtual": False,
            "assembly": False,
            "component": True,
            "purchaseable": True,
            "salable": False,
            "trackable": False,
            **kwargs,
        }
        
        # Add to the data (get_parts() above ensured the dataset is loaded)
        data = self._data
        if data is None:
            raise RuntimeError("Demo dataset failed to load")
        if "part" not in data:
            data["part"] = []
        data["part"].append(new_part)
        
        logger.info(f"Created new part in demo dataset: pk={new_pk}, name={name}")
        
        return new_part
    
    def search_parts(
        self,
        query: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """
        Search parts by name, description, or IPN.
        
        Args:
            query: Search query string
            limit: Maximum number of results
        
        Returns:
            List of matching parts
        """
        query_lower = query.lower()
        results = []
        
        for part in self.get_parts():
            # Check name, description, IPN - handle None values safely
            name = (part.get("name") or "").lower()
            description = (part.get("description") or "").lower()
            ipn = (part.get("IPN") or "").lower()
            
            if (query_lower in name or 
                query_lower in description or 
                query_lower in ipn):
                results.append(part)
                
                if len(results) >= limit:
                    break
        
        return results
    
    def get_parts_by_category(self, category_id: int) -> list[dict[str, Any]]:
        """Get all parts in a specific category."""
        return [
            part for part in self.get_parts()
            if part.get("category") == category_id
        ]
    
    # -------------------------------------------------------------------------
    # Stock Queries
    # -------------------------------------------------------------------------
    
    def get_stock_items(self, part_id: int | None = None) -> list[dict[str, Any]]:
        """
        Get stock items, optionally filtered by part.
        
        Args:
            part_id: Filter by part ID (optional)
        
        Returns:
            List of stock items
        """
        items = self.data.get("stock_stockitem", [])
        
        if part_id is not None:
            items = [item for item in items if item.get("part") == part_id]
        
        return items
    
    def get_stock_quantity(self, part_id: int) -> float:
        """Get total stock quantity for a part."""
        items = self.get_stock_items(part_id=part_id)
        return sum(item.get("quantity", 0) for item in items)
    
    def get_low_stock_parts(self, threshold: float | None = None) -> list[dict[str, Any]]:
        """
        Get parts with stock below minimum threshold.
        
        Args:
            threshold: Custom threshold (uses part's minimum_stock if None)
        
        Returns:
            List of parts with low stock
        """
        low_stock = []
        
        for part in self.get_parts():
            part_id = part.get("pk")
            min_stock = threshold or part.get("minimum_stock", 0)
            current_stock = self.get_stock_quantity(part_id)
            
            if current_stock < min_stock:
                low_stock.append({
                    **part,
                    "current_stock": current_stock,
                    "minimum_stock": min_stock,
                    "shortage": min_stock - current_stock,
                })
        
        return low_stock
    
    # -------------------------------------------------------------------------
    # Location Queries
    # -------------------------------------------------------------------------
    
    def get_locations(self) -> list[dict[str, Any]]:
        """Get all stock locations."""
        return self.data.get("stock_stocklocation", [])
    
    def get_location(self, location_id: int) -> dict[str, Any] | None:
        """Get a specific location by ID."""
        for location in self.get_locations():
            if location.get("pk") == location_id:
                return location
        return None
    
    def get_stock_at_location(self, location_id: int) -> list[dict[str, Any]]:
        """Get all stock items at a specific location."""
        return [
            item for item in self.data.get("stock_stockitem", [])
            if item.get("location") == location_id
        ]
    
    # -------------------------------------------------------------------------
    # Category Queries
    # -------------------------------------------------------------------------
    
    def get_categories(self) -> list[dict[str, Any]]:
        """Get all part categories."""
        return self.data.get("part_partcategory", [])
    
    def get_category(self, category_id: int) -> dict[str, Any] | None:
        """Get a specific category by ID."""
        for category in self.get_categories():
            if category.get("pk") == category_id:
                return category
        return None
    
    # -------------------------------------------------------------------------
    # BOM Queries
    # -------------------------------------------------------------------------
    
    def get_bom_items(self, part_id: int) -> list[dict[str, Any]]:
        """
        Get BOM (Bill of Materials) items for a part.
        
        Args:
            part_id: The parent part ID
        
        Returns:
            List of BOM items with sub-part details
        """
        bom_items = self.data.get("part_bomitem", [])
        
        result = []
        for item in bom_items:
            if item.get("part") == part_id:
                # Enrich with sub-part details
                sub_part = self.get_part(item.get("sub_part"))
                result.append({
                    **item,
                    "sub_part_name": sub_part.get("name") if sub_part else None,
                    "sub_part_description": sub_part.get("description") if sub_part else None,
                })
        
        return result
    
    def get_where_used(self, part_id: int) -> list[dict[str, Any]]:
        """
        Get assemblies where a part is used (reverse BOM lookup).
        
        Args:
            part_id: The sub-part ID to search for
        
        Returns:
            List of BOM items where this part is used
        """
        bom_items = self.data.get("part_bomitem", [])
        
        result = []
        for item in bom_items:
            if item.get("sub_part") == part_id:
                parent_part = self.get_part(item.get("part"))
                result.append({
                    **item,
                    "parent_name": parent_part.get("name") if parent_part else None,
                    "parent_description": parent_part.get("description") if parent_part else None,
                })
        
        return result
    
    # -------------------------------------------------------------------------
    # Company/Supplier Queries
    # -------------------------------------------------------------------------
    
    def get_companies(self) -> list[dict[str, Any]]:
        """Get all companies."""
        return self.data.get("company_company", [])
    
    def get_suppliers(self) -> list[dict[str, Any]]:
        """Get companies that are suppliers."""
        return [
            company for company in self.get_companies()
            if company.get("is_supplier")
        ]
    
    def get_supplier_parts(self, part_id: int) -> list[dict[str, Any]]:
        """Get supplier information for a part."""
        supplier_parts = self.data.get("company_supplierpart", [])
        
        result = []
        for sp in supplier_parts:
            if sp.get("part") == part_id:
                supplier = next(
                    (c for c in self.get_companies() if c.get("pk") == sp.get("supplier")),
                    None
                )
                result.append({
                    **sp,
                    "supplier_name": supplier.get("name") if supplier else None,
                })
        
        return result


# Singleton instance
_demo_provider: DemoDatasetProvider | None = None


def get_demo_provider() -> DemoDatasetProvider:
    """Get or create the shared demo dataset provider."""
    global _demo_provider
    if _demo_provider is None:
        _demo_provider = DemoDatasetProvider()
    return _demo_provider


def is_demo_mode() -> bool:
    """Check if demo mode is enabled."""
    settings = get_settings()
    return settings.use_demo_dataset
