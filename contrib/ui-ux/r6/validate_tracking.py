"""Validate R6 tracking without contacting services or changing files."""

import argparse
import csv
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def require(condition, message):
    """Fail validation even when Python assertions are optimized out."""
    if not condition:
        raise ValueError(message)


def read_rows(path):
    """Read a structurally valid CSV without silently dropping bad columns."""
    with path.open(newline='', encoding='utf-8') as stream:
        rows = list(csv.DictReader(stream))
    require(
        all(None not in row and None not in row.values() for row in rows),
        f'Malformed CSV: {path.name}',
    )
    return rows


def validate(reference=None):
    """Check coverage, dependencies, dispositions and local contract providers."""
    rows = read_rows(HERE / 'sprint-backlog.csv')
    tickets = {row['ticket']: row for row in rows}
    expected = {f'P{i:02}' for i in range(52)} | {f'L{i:02}' for i in range(1, 7)}
    require(len(rows) == 58 and set(tickets) == expected, 'Ticket coverage differs')
    require(
        sorted(int(row['worklist_item']) for row in rows) == list(range(1, 59)),
        'Worklist items must occur exactly once',
    )
    statuses = {
        'Planned',
        'In progress',
        'Blocked',
        'Ready for review',
        'Done',
        'Deferred',
    }
    for ticket, row in tickets.items():
        require(row['status'] in statuses, f'Unknown status: {ticket}')
        require(
            row['stage']
            in {f'S{stage_number}' for stage_number in range(9)} | {'Later'},
            f'Bad stage: {ticket}',
        )
        for dependency in row['prerequisites'].split():
            require(dependency in tickets, f'Unknown prerequisite: {ticket}')
            prior = tickets[dependency]
            if row['stage'] != 'Later':
                require(
                    prior['stage'] != 'Later' and prior['stage'] <= row['stage'],
                    f'Prerequisite scheduled after {ticket}: {dependency}',
                )
            if row['status'] == 'Done':
                require(
                    prior['status'] == 'Done', f'Unfinished prerequisite for {ticket}'
                )
    pending, completed = set(tickets), set()
    while pending:
        ready = {
            ticket
            for ticket in pending
            if set(tickets[ticket]['prerequisites'].split()) <= completed
        }
        require(bool(ready), 'Dependency cycle detected')
        completed.update(ready)
        pending.difference_update(ready)

    screens = read_rows(HERE / 'screen-plan.csv')
    require(
        sorted(int(row['screen']) for row in screens) == list(range(1, 96)),
        'Screen IDs must be 01-95 exactly once',
    )
    require(
        Counter(row['disposition'] for row in screens)
        == {'current': 88, 'later': 5, 'review-only': 1, 'retired': 1},
        'Screen release dispositions differ',
    )
    for row in screens:
        require(
            set(row['primary_tickets'].split()) <= set(tickets), 'Unknown screen ticket'
        )
        special = {
            '32': 'later',
            '33': 'later',
            '34': 'later',
            '35': 'later',
            '36': 'later',
            '47': 'review-only',
            '56': 'retired',
        }
        require(
            row['disposition'] == special.get(row['screen'], 'current'),
            'Screen scope changed',
        )

    contracts = read_rows(HERE / 'contracts.csv')
    require(
        len(contracts) == 26
        and {row['contract'] for row in contracts}
        == {f'D{i:02}' for i in range(1, 27)},
        'Contract coverage differs',
    )
    for row in contracts:
        for provider in row['provider_paths'].split(';'):
            require(
                (ROOT / provider).is_file(),
                f'Missing provider for {row["contract"]}: {provider}',
            )

    if reference:
        source = {
            row['Ticket']: row
            for row in read_rows(reference / 'EQUA_AIMMS_Implementation_Backlog_R6.csv')
        }
        require(set(source) == set(tickets), 'Source ticket IDs differ')
        for ticket, row in tickets.items():
            require(
                row['prerequisites'] == source[ticket]['Prerequisite tickets'],
                f'Source dependency drift: {ticket}',
            )
            require(
                row['acceptance_groups'] == source[ticket]['Acceptance group'],
                f'Acceptance drift: {ticket}',
            )
        source_screens = {
            row['Screen']: row
            for row in read_rows(reference / 'EQUA_AIMMS_Screen_Inventory_R6.csv')
        }
        for row in screens:
            require(
                row['areas'] == source_screens[row['screen']]['Blueprint area'],
                'Source area drift',
            )
    print(
        'PASS: 58 work items; 95 screen dispositions; 26 contracts; valid task dependencies and provider paths.'
    )


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--reference-dir',
        type=Path,
        help='Optional external R6 handoff for source cross-checks',
    )
    args = parser.parse_args()
    try:
        validate(args.reference_dir)
    except (ValueError, KeyError, OSError) as error:
        parser.exit(1, f'FAIL: {error}\n')
