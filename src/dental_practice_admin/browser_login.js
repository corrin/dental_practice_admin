exports.default = async ({ page }) => {
  const origin = process.env.PRINCIPLE_UI_URL;
  const slug = process.env.PRINCIPLE_WORKSPACE_SLUG;
  await page.goto(origin + '/login');
  const email = page.getByPlaceholder('Email', { exact: true });
  const workspace = page.getByPlaceholder('Search Workspaces');
  const ready = page.locator('pr-sidebar');
  await email.or(workspace).or(ready).first().waitFor({ timeout: 60000 });
  if (await email.isVisible()) {
    await email.fill(process.env.PRINCIPLE_UI_EMAIL);
    const password = page.getByPlaceholder('Password', { exact: true });
    await password.fill(process.env.PRINCIPLE_UI_PASSWORD);
    await password.press('Enter');
    await password.waitFor({ state: 'hidden', timeout: 30000 });
    await workspace.or(ready).first().waitFor({ timeout: 30000 });
  }
  if (await workspace.isVisible()) {
    await workspace.fill(process.env.PRINCIPLE_WORKSPACE.split(' ')[0]);
    await page.getByRole('option', { name: process.env.PRINCIPLE_WORKSPACE, exact: true }).click();
    await page.waitForURL(url => !url.pathname.includes('/login'), { timeout: 30000 });
  }
  await ready.waitFor({ timeout: 60000 });
  if (new URL(page.url()).origin !== origin || !new URL(page.url()).pathname.startsWith('/' + slug + '/'))
    throw new Error('Workspace selection failed');
  return { authenticated: true };
}
