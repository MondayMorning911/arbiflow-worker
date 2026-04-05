import https from 'https';
https.get('https://docs.socialkit.dev/authentication', (res) => {
  let data = '';
  res.on('data', (chunk) => data += chunk);
  res.on('end', () => console.log(data));
});
