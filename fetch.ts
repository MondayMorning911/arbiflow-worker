import https from 'https';
https.get('https://docs.socialkit.dev/api-reference/youtube-download-api', (res) => {
  let data = '';
  res.on('data', (chunk) => data += chunk);
  res.on('end', () => console.log(data));
});
