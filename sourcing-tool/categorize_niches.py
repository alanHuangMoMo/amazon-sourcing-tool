"""Categorize 1424 medical niches with extended keyword matching."""
import sys; sys.path.insert(0, '.')
from app.models import SessionLocal
from sqlalchemy import text

db = SessionLocal()
rows = db.execute(text(
    'SELECT niche_name, keyword_count, seed_keyword FROM niche WHERE niche_name IS NOT NULL'
)).fetchall()

categories = {
    '护具/矫形/支具': ['护具','护膝','护踝','护腕','护肘','护腰','矫形','夹板','支具','固定','矫正','托','髌骨','腱鞘','足弓','拇外翻','颈托','脊柱','骶髂','骨盆','疝气','束缚','姿势','跟腱','肩','关节','腰','膝','踝','腕','指','肘','背','颈','腿','臀','趾','甲沟','手臂吊带','行走靴','石膏','楔形','术后枕'],
    '伤口护理/敷料': ['敷料','绷带','纱布','创可贴','水胶体','伤口','止血','疤痕','胶带','造口','水泡','液体创可','免缝','组织胶','缝线','拆线','粘合','皮肤拉','痘痘贴','冷疮','硝酸银','皮肤粘','皮肤胶'],
    '注射/输液/针具': ['针','注射','输液','胰岛素','穿刺','针头','皮试','静脉','采血','留置','无针','针筒','针管','锐器','栓剂','给药'],
    '诊断/检测/监测': ['检测','试纸','监测','测试','诊断','体温计','血压','血氧','血糖','验孕','排卵','幽门','精子','尿液','听诊','脉搏','血酮','胆固醇','尿酸','心电','胎心','宫缩','糖化','CGM','菌斑显示'],
    '呼吸/气道': ['呼吸','CPAP','雾化','制氧','氧气','通气','气管','鼻罩','面罩','止鼾','鼻贴','吸氧','打鼾','气道','肺活量','峰流速','呼吸训练','蒸汽吸入','吸入器'],
    '鼻腔/鼻科护理': ['鼻','鼻腔','鼻窦','洗鼻','盐水','鼻喷','生理盐水','鼻塞','鼻屎','鼻涕','navage','neilmed','neti','hydrasense','rhinaris','sinus','salinex','xlear','xylitol','vicks','aerosal','nasal','rhino','鼻扩张','鼻通畅','鼻通','鼻吸','通鼻','鼻夹','扩鼻','鼻撑','鼻翼'],
    '康复/理疗/按摩': ['理疗','按摩','康复','冲击波','牵引','热敷','冷敷','冰敷','电刺激','磁疗','光疗','红外','穴位','筋膜','拔罐','刮痧','拉伸','TENS','中频','低频','超声波','激光','蜡疗','气压','振动','电热','神经刺激','迷走','PEMF','超声','电极片','冷疗','循环促进'],
    '医用压力袜/弹力袜': ['压力袜','弹力袜','压缩袜','静脉曲张','compression','sock','bas ','sigvaris','viasox','flytex','koprez','cambivo','czsalus','dr woof','calf','压缩','压力'],
    '牙科/口腔': ['牙','口腔','齿','矫正器','保持器','磨牙','牙套','冲牙','舌侧','正畸','假牙','美白','牙线','牙缝','牙龈','牙垢','牙石','氟斑','窝沟','漱口','洗牙','补牙','根管','种植','义齿','颌垫','咬合','扁桃体','tonsil'],
    '听力/耳科': ['听力','助听','耳','听诊','耳塞','耳垢','耵聍','耳鸣','中耳'],
    '视力/眼科': ['视力','眼镜','隐形','眼','泪液','洗眼','人工泪','眼压','散瞳','验光','角膜','巩膜','睑板','睫毛','眼睑','色盲','弱视','斜视','老花','近视','晕动','防晕','motion'],
    '移动辅具/无障碍': ['轮椅','拐杖','助行','扶手','移位','洗澡椅','坐便','助步','代步','爬楼','护理床','防褥疮','防压疮','便盆','尿壶','起身','手杖','四脚拐','肘拐','腋拐','学步','转移','升降','吊架','移位机','马桶增高','沐浴椅','淋浴','洗浴','床护栏','病床桌','门槛','坡道','助取','拾物','穿袜','药丸粉碎','食品增稠'],
    '防护/消毒': ['口罩','手套','消毒','灭菌','防护','隔离','酒精','清洁','杀菌','抗菌','抑菌','紫外线','等离子','环氧','戊二醛','过氧化','碘伏','洗必泰','新洁尔','来苏','洗手液','手消','免洗','手消毒'],
    '婴儿鼻腔护理': ['吸鼻','鼻吸','吸鼻器','鼻器','booger','snot','mouche','frida','nose suck','nose pick','nose clean','momcozy','growsny','oogie','nasal aspirator','鼻屎夹','鼻垢夹','鼻洁','婴儿鼻','baby nose','baby nasal'],
    '吸乳/哺乳/产后': ['吸乳','吸奶','哺乳','乳头','产后','防溢乳','母乳','储奶','泌乳','催乳','开奶','通乳','奶瓶','奶嘴','乳盾','乳贴','乳房','乳腺','涨奶','回奶','产房','分娩','会阴','peri bottle','会阴冲洗','坐浴','sitz'],
    '急救/应急': ['急救','应急','first aid','救生','生存','survival','医疗包','急救包','急救箱','急救袋','医疗箱','ifak','emergency','急救','创伤','trauma','窒息','choking','life vac','除颤','AED','呕吐袋','emesis','puke','vomit'],
    '皮肤/足部/标签去除': ['鸡眼','老茧','足跟','趾甲','嵌甲','真菌','疣','灰指甲','去茧','修脚','脚气','脚臭','脚汗','脚垫','跟痛','足底筋','扁平足','高弓足','拇囊','锤状趾','槌状趾','爪状趾','大脚骨','皮肤标签','skin tag','mole remover','plasma pen','nuzzy','疣去除','liquid nitrogen','冷冻','wart','退热贴','fever patch'],
    '泌尿/肛肠/失禁': ['导尿','失禁','灌肠','结肠','尿','痔疮','盆底','膀胱','肾','前列腺','包皮','阴道','子宫','宫颈','凯格尔','括约','肛门','直肠','便失','尿失','漏尿','护理垫','soaker','pee pad','失禁垫','隔尿垫','坐骨','donut','尾骨','坐垫'],
    '兽医': ['兽医','宠物','犬','猫','马','牛','羊','鸡','兔','鼠','鸟','鱼','龟','蜥蜴','动物','驱虫','耳螨','跳蚤','蜱','心丝','球虫','弓形','绦虫','蛔虫','钩虫','鞭虫','虱','螨'],
    '人工授精/备孕': ['授精','insemination','精子','sperm','备孕','fertility'],
}

categorized = {k: [] for k in categories}
uncategorized = []

for name, kw_count, seed in rows:
    found = False
    for cat, patterns in categories.items():
        for p in patterns:
            if p.lower() in name.lower():
                categorized[cat].append((name, kw_count, seed))
                found = True
                break
        if found:
            break
    if not found:
        uncategorized.append((name, kw_count, seed))

# Print stats
total_kw = sum(r[1] for r in rows)
print(f'=== 医疗器械赛道分布 ({len(rows)} niches, {total_kw} 关键词) ===')
print()
print(f'{"Category":35s} {"Niches":>7s} {"Keywords":>9s}  Max KW')
print('-' * 65)
for cat in categories:
    items = categorized[cat]
    if not items:
        continue
    kw_sum = sum(it[1] for it in items)
    max_kw = max(it[1] for it in items)
    print(f'{cat:35s} {len(items):7d} {kw_sum:9d} {max_kw:7d}')

if uncategorized:
    ukw = sum(it[1] for it in uncategorized)
    print(f'{"Uncategorized":35s} {len(uncategorized):7d} {ukw:9d}')

# Write detailed report
with open('data/niche_summary.txt', 'w', encoding='utf-8') as f:
    f.write(f'=== 医疗器械赛道分布 ({len(rows)} niches, {total_kw} 关键词) ===\n\n')
    for cat in categories:
        items = categorized[cat]
        if not items:
            continue
        kw_sum = sum(it[1] for it in items)
        largest = max(it[1] for it in items) if items else 0
        f.write(f'【{cat}】{len(items)} 赛道, {kw_sum} 关键词 (最大={largest}kw)\n')
        for name, kc, seed in sorted(items, key=lambda x: -x[1])[:5]:
            f.write(f'  {kc:4d} kw  {name:20s} (seed: {seed[:60]})\n')
        f.write('\n')

    if uncategorized:
        f.write(f'=== 未分类 ({len(uncategorized)} 个) ===\n')
        for name, kc, seed in uncategorized:
            f.write(f'  {kc:4d} kw  {name:20s} (seed: {seed[:60]})\n')

print('\nWrote detailed report to data/niche_summary.txt')
db.close()
