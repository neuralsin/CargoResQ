# CargoResQ Driver

Dedicated Android driver companion for field operators. It uses the same restrained white / ink / teal visual system as the operations console and calls the production API directly.

## Development

```bash
npm install
EXPO_PUBLIC_API_BASE_URL=https://api.cargoresq.com npx expo start
```

For a local API during development, set `EXPO_PUBLIC_API_BASE_URL` to the machine-reachable API origin (for a physical device, use the machine's LAN address rather than a loopback address).

## Android release

```bash
npx eas login
npx eas build:configure
npm run build:android
npm run submit:android
```

The production profile creates an Android App Bundle. Before Play submission, configure the EAS project ID, Android package signing, store listing, privacy-policy URL, support email, and production API secret/configuration in the EAS dashboard.
