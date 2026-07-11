"""
AIMMS Workflow Test Runner

Test script to validate workflows using the demo dataset.
Run this script to verify that all workflows are functioning correctly
with the InvenTree demo data.

Usage:
    python -m core.tests.test_workflows
    
    # Or run specific tests:
    python -m core.tests.test_workflows --workflow wf8
    python -m core.tests.test_workflows --all
"""

from __future__ import annotations

import asyncio
import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def print_header(title: str) -> None:
    """Print a formatted header."""
    print("\n" + "=" * 70)
    print(f" {title}")
    print("=" * 70)


def print_result(success: bool, message: str, details: Any = None) -> None:
    """Print a test result."""
    status = "✅ PASS" if success else "❌ FAIL"
    print(f"\n{status}: {message}")
    if details:
        if isinstance(details, dict):
            print(json.dumps(details, indent=2, default=str)[:500])
        else:
            print(str(details)[:500])


class WorkflowTester:
    """Test runner for AIMMS workflows using demo dataset."""
    
    def __init__(self):
        """Initialize the tester."""
        self.results: list[dict[str, Any]] = []
        self.demo_provider = None
    
    async def setup(self) -> bool:
        """Set up test environment."""
        print_header("Setting Up Test Environment")
        
        try:
            # Load demo dataset
            from ai.core.integrations.demo_dataset import (
                get_demo_provider,
                is_demo_mode,
            )
            from ai.core.config import get_settings
            
            settings = get_settings()
            print(f"Environment: {settings.env}")
            print(f"Demo mode enabled: {settings.use_demo_dataset}")
            print(f"Demo dataset path: {settings.demo_dataset_json}")
            
            self.demo_provider = get_demo_provider()
            stats = self.demo_provider.get_statistics()
            
            print(f"\nDemo Dataset Statistics:")
            for key, value in stats.items():
                print(f"  - {key}: {value}")
            
            print_result(True, "Test environment setup complete")
            return True
            
        except Exception as e:
            print_result(False, f"Setup failed: {e}")
            return False
    
    async def test_demo_dataset_queries(self) -> bool:
        """Test basic demo dataset queries."""
        print_header("Testing Demo Dataset Queries")
        
        try:
            # Test 1: Search parts
            print("\n📦 Test 1: Searching for 'motor' parts...")
            parts = self.demo_provider.search_parts("motor", limit=5)
            print_result(
                len(parts) > 0,
                f"Found {len(parts)} parts matching 'motor'",
                {"sample_part": parts[0] if parts else None}
            )
            self.results.append({"test": "search_parts", "success": len(parts) > 0})
            
            # Test 2: Get stock items
            print("\n📊 Test 2: Getting stock items...")
            if parts:
                part_id = parts[0].get("pk")
                stock = self.demo_provider.get_stock_items(part_id=part_id)
                quantity = self.demo_provider.get_stock_quantity(part_id)
                print_result(
                    True,
                    f"Part {part_id} has {len(stock)} stock entries, total qty: {quantity}"
                )
                self.results.append({"test": "get_stock", "success": True})
            
            # Test 3: Get categories
            print("\n🗂️ Test 3: Getting categories...")
            categories = self.demo_provider.get_categories()
            print_result(
                len(categories) > 0,
                f"Found {len(categories)} categories",
                {"sample_category": categories[0] if categories else None}
            )
            self.results.append({"test": "get_categories", "success": len(categories) > 0})
            
            # Test 4: Get locations
            print("\n📍 Test 4: Getting stock locations...")
            locations = self.demo_provider.get_locations()
            print_result(
                len(locations) > 0,
                f"Found {len(locations)} locations"
            )
            self.results.append({"test": "get_locations", "success": len(locations) > 0})
            
            # Test 5: Get BOM items
            print("\n🔧 Test 5: Searching for assemblies with BOM...")
            all_parts = self.demo_provider.get_parts()
            assembly_found = False
            for part in all_parts[:50]:  # Check first 50 parts
                bom = self.demo_provider.get_bom_items(part.get("pk"))
                if bom:
                    print_result(
                        True,
                        f"Found assembly '{part.get('name')}' with {len(bom)} BOM items",
                        {"bom_sample": bom[0] if bom else None}
                    )
                    assembly_found = True
                    break
            
            if not assembly_found:
                print_result(False, "No assemblies with BOM found")
            self.results.append({"test": "get_bom", "success": assembly_found})
            
            # Test 6: Get low stock parts
            print("\n⚠️ Test 6: Checking for low stock parts...")
            low_stock = self.demo_provider.get_low_stock_parts()
            print_result(
                True,
                f"Found {len(low_stock)} parts with low stock",
                {"sample": low_stock[0] if low_stock else "No low stock items"}
            )
            self.results.append({"test": "low_stock", "success": True})
            
            # Test 7: Get suppliers
            print("\n🏭 Test 7: Getting suppliers...")
            suppliers = self.demo_provider.get_suppliers()
            print_result(
                len(suppliers) > 0,
                f"Found {len(suppliers)} suppliers",
                {"sample_supplier": suppliers[0].get("name") if suppliers else None}
            )
            self.results.append({"test": "get_suppliers", "success": len(suppliers) > 0})
            
            return all(r["success"] for r in self.results)
            
        except Exception as e:
            print_result(False, f"Query tests failed: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    async def test_router_classification(self) -> bool:
        """Test router agent intent classification."""
        print_header("Testing Router Agent Classification")
        
        try:
            from ai.core.agents.routing import FastPathRouter
            
            # Test T1 fast-path patterns
            print("\n🎯 Testing T1 Fast-Path Patterns...")
            
            test_queries = [
                ("How much stock do we have for MOT-001?", "lookup", True),
                ("What parts are in warehouse A?", "lookup", True),
                ("Find me information about capacitors", "lookup", True),
                ("The motor is overheating and making noise", "diagnostics", False),
                ("Create a purchase order for 100 resistors", "procurement", False),
                ("Generate a quote for the assembly project", "cpq", False),
            ]
            
            router = FastPathRouter()
            patterns = router.compile_patterns()

            def match_fast_path_category(query: str) -> str | None:
                q = query.lower().strip()
                for category, category_patterns in patterns.items():
                    for pattern in category_patterns:
                        if pattern.search(q):
                            return category
                return None
            
            for query, expected_intent, expect_fast_path in test_queries:
                category = match_fast_path_category(query)
                
                if expect_fast_path:
                    success = category is not None
                    print_result(
                        success,
                        f"Fast-path for '{query[:40]}...'",
                        {"detected": category}
                    )
                else:
                    success = category is None
                    print_result(
                        success,
                        f"No fast-path for '{query[:40]}...' (requires LLM)"
                    )
                
                self.results.append({
                    "test": f"router_{expected_intent}",
                    "success": success
                })
            
            return all(r["success"] for r in self.results if "router" in r["test"])
            
        except Exception as e:
            print_result(False, f"Router tests failed: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    async def test_event_emission(self) -> bool:
        """Test AG-UI event emission."""
        print_header("Testing AG-UI Event Emission")
        
        try:
            from ai.core.events import (
                get_event_emitter,
                create_run_context,
                EventType,
                EventCollector,
            )
            
            # Set up event collection
            emitter = get_event_emitter()
            collector = EventCollector()
            await emitter.subscribe(collector)
            
            # Create a run context and emit events
            print("\n📡 Emitting test events...")
            
            run = create_run_context(
                thread_id="test-thread-001",
                agent_name="TestAgent",
                workflow_id="wf8_lookup",
            )
            
            await run.emit_thinking("Analyzing query...")
            await run.emit_tool_started("search_parts", {"query": "motor"})
            await run.emit_tool_completed("search_parts", ["Part 1", "Part 2"])
            await run.emit_progress(1, 3, "Step 1 complete")
            await run.emit_progress(2, 3, "Step 2 complete")
            await run.emit_progress(3, 3, "Step 3 complete")
            await run.emit_completed("Query processed successfully")
            
            # Verify events were collected
            all_events = collector.events
            print_result(
                len(all_events) >= 6,
                f"Collected {len(all_events)} events",
                {"event_types": [e.event_type.value for e in all_events]}
            )
            
            # Check specific event types
            thinking_events = collector.get_events(event_type=EventType.AGENT_THINKING)
            tool_events = collector.get_events(event_type=EventType.TOOL_CALL_START)
            progress_events = collector.get_events(event_type=EventType.PROGRESS_UPDATE)
            
            print_result(
                len(thinking_events) == 1,
                f"Thinking events: {len(thinking_events)}"
            )
            print_result(
                len(tool_events) == 1,
                f"Tool call events: {len(tool_events)}"
            )
            print_result(
                len(progress_events) == 3,
                f"Progress events: {len(progress_events)}"
            )
            
            self.results.append({"test": "event_emission", "success": len(all_events) >= 6})
            
            # Clean up
            emitter.clear_handlers()
            collector.clear()
            
            return True
            
        except Exception as e:
            print_result(False, f"Event tests failed: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    async def test_semantic_cache(self) -> bool:
        """Test semantic cache with HITL safety rules."""
        print_header("Testing Semantic Cache")
        
        try:
            from ai.core.memory.semantic_cache import (
                HITLSafetyRules,
                CacheConfig,
                SemanticCache,
            )
            
            # Test HITL safety rules
            print("\n🔒 Testing HITL Safety Rules...")
            
            test_cases = [
                ("What parts do we have in stock?", True, "Safe query"),
                ("Approve this purchase order", False, "HITL pattern"),
                ("What is the current stock level?", False, "Time-sensitive"),
                ("Delete all records", False, "HITL pattern"),
                ("Show me motor specifications", True, "Safe query"),
            ]
            
            for query, should_cache, reason in test_cases:
                can_cache, rule_reason = HITLSafetyRules.can_cache(query)
                success = can_cache == should_cache
                print_result(
                    success,
                    f"'{query[:35]}...' - {reason}",
                    {"can_cache": can_cache, "reason": rule_reason}
                )
                self.results.append({
                    "test": f"hitl_safety_{reason.replace(' ', '_')}",
                    "success": success
                })
            
            return all(r["success"] for r in self.results if "hitl_safety" in r["test"])
            
        except Exception as e:
            print_result(False, f"Cache tests failed: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    async def test_middleware(self) -> bool:
        """Test middleware error categorization."""
        print_header("Testing Middleware")
        
        try:
            from ai.core.middleware import (
                ReflectionFunctionMiddleware,
                ErrorCategory,
            )
            
            middleware = ReflectionFunctionMiddleware(reflection_enabled=False)
            
            # Test error categorization
            print("\n🔍 Testing Error Categorization...")
            
            test_errors = [
                (Exception("Connection timeout"), ErrorCategory.TRANSIENT_INFRA),
                (Exception("Rate limit exceeded 429"), ErrorCategory.TRANSIENT_INFRA),
                (ValueError("Invalid parameter"), ErrorCategory.VALIDATION),
                (Exception("Permission denied"), ErrorCategory.BUSINESS_RULE),
                (Exception("Random unknown error"), ErrorCategory.UNKNOWN),
            ]
            
            for error, expected_category in test_errors:
                category = middleware.categorize_error(error)
                success = category == expected_category
                print_result(
                    success,
                    f"'{str(error)[:30]}' -> {category.value}",
                    {"expected": expected_category.value}
                )
                self.results.append({
                    "test": f"middleware_{expected_category.value}",
                    "success": success
                })
            
            return all(r["success"] for r in self.results if "middleware" in r["test"])
            
        except Exception as e:
            print_result(False, f"Middleware tests failed: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def print_summary(self) -> None:
        """Print test summary."""
        print_header("Test Summary")
        
        passed = sum(1 for r in self.results if r["success"])
        failed = sum(1 for r in self.results if not r["success"])
        total = len(self.results)
        
        print(f"\n📊 Results: {passed}/{total} tests passed")
        print(f"   ✅ Passed: {passed}")
        print(f"   ❌ Failed: {failed}")
        
        if failed > 0:
            print("\n❌ Failed tests:")
            for r in self.results:
                if not r["success"]:
                    print(f"   - {r['test']}")
        
        print("\n" + "=" * 70)
        
        if failed == 0:
            print("🎉 All tests passed! The system is ready for use.")
        else:
            print("⚠️ Some tests failed. Please review the errors above.")


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="AIMMS Workflow Test Runner")
    parser.add_argument(
        "--workflow",
        choices=["wf1", "wf2", "wf3", "wf4", "wf5", "wf6", "wf8", "all"],
        default="all",
        help="Specific workflow to test (default: all)"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose output"
    )
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    print("\n" + "=" * 70)
    print(" AIMMS Workflow Test Runner")
    print(" Testing with InvenTree Demo Dataset")
    print("=" * 70)
    print(f"\n🕐 Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    tester = WorkflowTester()
    
    # Run setup
    if not await tester.setup():
        print("\n❌ Setup failed. Exiting.")
        sys.exit(1)
    
    # Run tests
    await tester.test_demo_dataset_queries()
    await tester.test_router_classification()
    await tester.test_event_emission()
    await tester.test_semantic_cache()
    await tester.test_middleware()
    
    # Print summary
    tester.print_summary()
    
    # Exit with appropriate code
    failed = sum(1 for r in tester.results if not r["success"])
    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    asyncio.run(main())
