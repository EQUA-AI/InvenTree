import type { Page } from '@playwright/test';

export async function mockPump08Staff(page: Page) {
  const trade = { pk: 2, code: 'electrical', name: 'Electrical', active: true };
  const people = [
    {
      user_id: 27,
      display_name: 'Avery Patel',
      job_title: 'Electrical technician',
      trades: [trade]
    },
    {
      user_id: 29,
      display_name: 'Daniel Chen',
      job_title: 'Electrical technician',
      trades: [trade]
    }
  ];
  await page.route('**/api/user/staff-directory/?*', (route) => {
    const selected = new URL(route.request().url()).searchParams.get('user_id');
    const results = selected
      ? people.filter((person) => String(person.user_id) === selected)
      : people;
    return route.fulfill({ json: { count: results.length, results } });
  });
  await page.route('**/api/user/trades/?*', (route) =>
    route.fulfill({ json: { count: 1, results: [trade] } })
  );
}
