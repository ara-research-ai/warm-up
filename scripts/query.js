const connection = new Mongo(process.env.MONGODB_URI);
const now = new Date();
const accounts = connection.getDB('ara-whatsapp').whatsappaccounts.find({
  status: {$ne: 'removed'},
  // List-only exclusion: preserve DUITGINI SIM and client status.
  business_id: {$ne: ObjectId('692572bc4d963400a9952ed8')},
  $or: [
    {status:'warmup'}, {status:'cooldown'},
    {'sim_recovery.state':'cooldown'},
    {'sim_recovery.cooldown_until':{$gt:now}},
    {'warmup.state':{$in:['cooldown','ready_to_activate']}}
  ]
}, {name:1,phone_number:1,business_id:1,status:1,'warmup.state':1,'warmup.started_at':1,'warmup.completed_days':1,'sim_recovery.state':1,'sim_recovery.cooldown_until':1})
  .limit(101).maxTimeMS(15000).toArray();
if (accounts.length > 100) throw new Error('Account query bound exceeded');
const businesses = connection.getDB('ara-user').businesses.find(
  {_id:{$in:accounts.map(account=>account.business_id)}},
  {name:1,client_status:1}
).limit(101).maxTimeMS(15000).toArray();
const active = new Map(businesses.filter(b=>b.client_status==='active').map(b=>[b._id.toString(),b]));
// Calendar display only; completed-day credits and activation gates stay unchanged.
const calendarIds = new Set([
  '6ad9d6eea66327103645ea92', '6ad9d7e5a66327103645ec5b',
  '6ad9d83aa66327103645ecdd', '6ad9d8d1a66327103645ee5e',
  '6ad9dbf3a66327103645f3d6'
]);
const rows = accounts.filter(a=>a.business_id&&active.has(a.business_id.toString())).map(a=>{
  const cooldown = a.status==='cooldown'||a.sim_recovery?.state==='cooldown'||a.warmup?.state==='cooldown'||a.sim_recovery?.cooldown_until>now;
  const group = cooldown?'cooldown':a.warmup?.state==='ready_to_activate'?'ready':'warmup';
  const calendar = group==='warmup' && calendarIds.has(a._id.toString());
  return {name:a.name, number:a.phone_number, group, day:group==='warmup'?(a.warmup?.completed_days??0)+1:null,
    ...(calendar ? {calendarStartedAt: JSON.parse(EJSON.stringify(a)).warmup?.started_at?.$date ?? null} : {})};
});
print(JSON.stringify({capturedAt:new Date().toISOString(),rows}));
