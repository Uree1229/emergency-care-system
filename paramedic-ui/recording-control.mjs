/** Recording commands are client controls, never clinical text or server commands. */
export const RECORDING_LIMITS = Object.freeze({toggle:180, hold:30, text:4000});

// Only a terminal, explicit command is accepted. In particular, Korean particles
// and suffixes are not word boundaries (그만큼, 종료시각, 종료하지 마, 그만이라고).
const COMMAND = /(?:^|[\s,，.。!！?？;；:：])((?:(?:녹음|음성(?:\s*입력)?|말하기)\s*(?:그만|종료)|그만|종료)(?:\s*(?:해\s*주세요|해\s*줘|해주세요|해줘|하겠습니다|할게요|할게|합니다|하자|해))?)[\s,.。!！?？;；]*$/u;
const CLINICAL_END = /(?:CPR|심폐\s*소생술|소생술|처치|시술|수술|치료|투약|주입|수액|이송|검사|제세동|압박|환기|분만|경련|발작|출혈|통화|면담)(?:을|를|이|가|는|은)?\s*$/iu;
const REPORTED = /(?:환자|보호자|동승자|목격자)(?:가|는|분이|분은)?\s*(?:(?:이제|여기서|말하기를|진술|말을?|하던\s*말을)\s*)*$/u;
const OPEN_QUOTES = new Map([['“','”'],['‘','’'],['「','」'],['『','』'],['"','"'],["'","'"]]);

function insideQuote(text, end) {
  const stack=[];
  for(const char of text.slice(0,end)) {
    if(stack.length&&char===stack.at(-1))stack.pop();
    else if(OPEN_QUOTES.has(char))stack.push(OPEN_QUOTES.get(char));
  }
  return stack.length>0;
}

/** Return the original prefix and exact command span, or null. Never inspect interim STT. */
export function splitStopCommand(value, {isFinal=true}={}) {
  if(!isFinal||typeof value!=='string')return null;
  const match=COMMAND.exec(value);
  if(!match)return null;
  const index=match.index+match[0].indexOf(match[1]);
  const prefix=value.slice(0,index).trimEnd(), command=match[1];
  if(insideQuote(value,index)||/[“”‘’"'「」『』]/u.test(command))return null;
  if(REPORTED.test(prefix))return null;
  if(/^(?:종료|그만)/u.test(command)&&CLINICAL_END.test(prefix))return null;
  return {prefix,command,index,end:index+command.length};
}
